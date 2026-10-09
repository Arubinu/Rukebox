#!/bin/bash
set -u

CONFIG_FILE="/etc/rukebox/rukebox.env"

CHECK_SECONDS=3
# The gap between byte-counter reads is the delay before a silent link is noticed.
CONNECTED_TICKS=3
SLOW_TICKS=20
BACKOFF_TICKS=(1 2 3 5 10 20)
CALL_TIMEOUT=10
# A connect on a jammed controller blocks past CALL_TIMEOUT: killed early, it never says why.
CONNECT_TIMEOUT=30
REPAIR_AFTER_FAILURES=3
# Seconds, not ticks: the loop's pace changes with the branch it is in.
REPAIR_SECONDS=300
# The controller's byte counter must move while the radio plays: frozen this long means the link is dead.
SILENT_SECONDS=15
SILENT_MIN_BYTES=2000
# The built-in chip is the Wi-Fi's radio too: paging an absent speaker there drops the Wi-Fi link.
SHARED_CALM_TICKS=100
SHARED_PAGE_SLOTS=4096

SPEAKER_MAC=""
ADAPTER=""

reload_config() {
    if [ -f "$CONFIG_FILE" ]; then
        # shellcheck disable=SC1090
        . "$CONFIG_FILE"
    fi
    SPEAKER_MAC="${SPEAKER_MAC:-}"
    ADAPTER="${SPEAKER_BT_ADAPTER:-}"
    # A configuration brought from another Pi names a controller this one does not have.
    case "$ADAPTER" in
        ??:??:??:??:??:??)
            if [ -z "$(adapter_index)" ]; then
                [ "$MISSING_ADAPTER" != "$ADAPTER" ] \
                    && echo "Controller $ADAPTER is not on this Pi: using the default one."
                MISSING_ADAPTER="$ADAPTER"
                ADAPTER=""
            fi ;;
    esac
}
MISSING_ADAPTER=""

adapter_index() {
    case "$ADAPTER" in
        hci*) echo "${ADAPTER#hci}" ;;
        ??:??:??:??:??:??)
            hciconfig 2>/dev/null | awk -v mac="$(echo "$ADAPTER" | tr 'a-f' 'A-F')" '
                /^hci[0-9]+:/ { dev = $1; sub(":", "", dev); sub("hci", "", dev) }
                /BD Address:/ { if (toupper($3) == mac) print dev }' | head -1 ;;
    esac
}

# The configured controller, or BlueZ's default - the one it connects the speaker through.
controller_index() {
    local index address
    index="$(adapter_index)"
    if [ -n "$index" ]; then
        echo "$index"
        return 0
    fi
    address="$(bluetoothctl list 2>/dev/null | awk '/\[default\]/ { print $2; exit }')"
    if [ -n "$address" ]; then
        hciconfig 2>/dev/null | awk -v mac="$(echo "$address" | tr 'a-f' 'A-F')" '
            /^hci[0-9]+:/ { dev = $1; sub(":", "", dev); sub("hci", "", dev) }
            /BD Address:/ { if (toupper($3) == mac) print dev }' | head -1
    fi
}

shared_radio() {
    local index bus
    index="$(controller_index)"
    [ -z "$index" ] && return 1
    bus="$(hciconfig "hci${index}" 2>/dev/null | awk '/Bus:/ { for (i = 1; i < NF; i++) if ($i == "Bus:") print $(i + 1); exit }')"
    [ -n "$bus" ] && [ "$bus" != "USB" ]
}

# Half the default page: short enough for the Wi-Fi to keep its access point, long enough to reach a speaker.
shorten_page() {
    local index
    index="$(controller_index)"
    [ -n "$index" ] && hciconfig "hci${index}" pageto "$SHARED_PAGE_SLOTS" 2>/dev/null || true
}

# btmgmt hangs with stdin on /dev/null (what systemd gives): it needs a pipe.
# Aimed at the speaker's controller: the default one may belong to flicd.
btmgmt_cmd() {
    local index
    index="$(adapter_index)"
    if [ -n "$index" ]; then
        timeout "$CALL_TIMEOUT" btmgmt --index "$index" "$@" < <(sleep "$CALL_TIMEOUT")
    elif [ -z "$ADAPTER" ]; then
        timeout "$CALL_TIMEOUT" btmgmt "$@" < <(sleep "$CALL_TIMEOUT")
    fi
}

ensure_connectable() {
    local settings
    settings=$(btmgmt_cmd info 2>/dev/null | sed -n 's/.*current settings: //p')
    if [ -z "$settings" ]; then
        # An empty answer is not "not connectable": the controller needs repairing instead.
        echo "The Bluetooth controller did not answer btmgmt."
        return 1
    fi
    case " $settings " in
        *" connectable "*) return 0 ;;
    esac
    echo "Adapter was not connectable (settings: ${settings}), enabling it so the speaker can reconnect on its own."
    btmgmt_cmd connectable on >/dev/null 2>&1 || true
}

# A jammed controller still answers btmgmt and refuses every operation; only a reset clears it.
# Only its own timeouts count: "link tx timeout" is a speaker that is off or out of range.
hci_stuck_marks() {
    local index
    index="$(controller_index)"
    if [ -z "$index" ]; then
        echo 0
        return 0
    fi
    dmesg 2>/dev/null | grep -cE \
        "hci${index}: command .* tx timeout|hci${index}: Opcode .* failed: -110" || true
}

# A plain hciconfig down/up leaves the controller DOWN: bluetoothd must be stopped around it.
repair_controller() {
    local index name
    index="$(controller_index)"
    if [ -z "$index" ]; then
        echo "Cannot work out which controller to repair (adapter: '${ADAPTER:-automatic}')."
        return 1
    fi
    name="hci${index}"
    echo "Repairing ${name}: the kernel got no answer to its last commands, so no connection can be made."
    systemctl stop bluetooth 2>/dev/null || true
    sleep 2
    hciconfig "$name" down 2>/dev/null || true
    sleep 2
    hciconfig "$name" up 2>/dev/null || true
    sleep 3
    systemctl start bluetooth 2>/dev/null || true
    sleep 4
    hciconfig "$name" up 2>/dev/null || true
    sleep 2
    btmgmt_cmd connectable on >/dev/null 2>&1 || true
    echo "${name} restarted."
    return 0
}

is_connected() {
    timeout "$CALL_TIMEOUT" bluetoothctl info "$SPEAKER_MAC" 2>/dev/null \
        | grep -q "Connected: yes"
}

# Grows whenever anything streams - a silence is still SBC frames - so a frozen counter means silence.
hci_tx_bytes() {
    local index
    index="$(controller_index)"
    [ -z "$index" ] && return 0
    hciconfig "hci${index}" 2>/dev/null \
        | sed -n 's/.*TX bytes:\([0-9]*\).*/\1/p' | head -1
}

# A frozen counter while paused is not a fault: only repair when the daemon says it is playing.
radio_is_playing() {
    python3 - "${CONTROL_SOCKET:-/tmp/rukebox_control.sock}" <<'PY' 2>/dev/null
import json
import socket
import sys

try:
    sock = socket.socket(socket.AF_UNIX)
    sock.settimeout(4)
    sock.connect(sys.argv[1])
    sock.sendall(b'{"cmd": "get_status"}\n')
    state = json.loads(sock.recv(65536).decode()).get("data") or {}
except Exception:
    raise SystemExit(1)
mode = str(state.get("mode") or "")
busy = mode not in ("", "idle", "stopped", "shutting_down")
print("yes" if busy and not state.get("paused") else "no")
PY
}

# The jam's own answers; br-connection-refused is not one of them - that is the speaker on a phone.
CONNECT_OUT=""
connect_refused() {
    case "$CONNECT_OUT" in
        *br-connection-busy*|*org.bluez.Error.InProgress*|*"Operation already in progress"*) return 0 ;;
    esac
    return 1
}

request_connect() {
    if [ -n "$ADAPTER" ]; then
        CONNECT_OUT="$(printf 'select %s\nconnect %s\n' "$ADAPTER" "$SPEAKER_MAC" \
            | timeout "$CONNECT_TIMEOUT" bluetoothctl 2>&1 || true)"
    else
        CONNECT_OUT="$(printf 'connect %s\n' "$SPEAKER_MAC" \
            | timeout "$CONNECT_TIMEOUT" bluetoothctl 2>&1 || true)"
    fi
}

tick=0
failures=0
next_attempt=0
connected=0
warned_unconfigured=0
warned_network=0
warned_calm=0
connect_reported=0
shared=0
healthy_marks=0
last_repair_time=-$((REPAIR_SECONDS * 2))
healthy_marks=$(hci_stuck_marks)
last_tx=""
silent_since=$(date +%s)

while :; do
    tick=$((tick + 1))

    if [ $((tick % SLOW_TICKS)) -eq 1 ]; then
        reload_config
        ensure_connectable
        if shared_radio; then
            shared=1
            shorten_page
        else
            shared=0
        fi
    fi

    if [ -z "$SPEAKER_MAC" ] || [ "$SPEAKER_MAC" = "XX:XX:XX:XX:XX:XX" ]; then
        if [ "$warned_unconfigured" -eq 0 ]; then
            echo "No speaker configured (speaker_mac in /etc/rukebox/rukebox.yaml) - waiting for one."
            warned_unconfigured=1
        fi
        sleep "$CHECK_SECONDS"
        continue
    fi

    # The multiroom output needs the Wi-Fi: paging an absent speaker would hold it up for seconds.
    if [ "${AUDIO_OUTPUT:-bluetooth}" = "snapcast" ] && ! is_connected; then
        if [ "$warned_network" -eq 0 ]; then
            echo "The sound goes to the multiroom output: no attempt to reach $SPEAKER_MAC meanwhile."
            warned_network=1
        fi
        connected=0
        sleep "$CHECK_SECONDS"
        continue
    fi
    warned_network=0

    if is_connected; then
        if [ "$connected" -eq 0 ]; then
            echo "Connected to $SPEAKER_MAC."
            connected=1
        fi
        failures=0
        warned_calm=0
        connect_reported=0
        healthy_marks=$(hci_stuck_marks)
        next_attempt=$((tick + 1))

        # Connected, with a byte counter that never moves: nothing but a fresh link clears it.
        now=$(date +%s)
        now_tx=$(hci_tx_bytes)
        if [ -z "$now_tx" ] || [ -z "$last_tx" ] || [ "$now_tx" -lt "$last_tx" ]; then
            last_tx=$now_tx            # first reading, or the counter was reset
            silent_since=$now
        elif [ $((now_tx - last_tx)) -ge "$SILENT_MIN_BYTES" ]; then
            last_tx=$now_tx
            silent_since=$now
        elif [ $((now - silent_since)) -ge "$SILENT_SECONDS" ] \
                && [ $((now - last_repair_time)) -ge "$REPAIR_SECONDS" ]; then
            if [ "$(radio_is_playing)" = "yes" ]; then
                echo "The speaker is connected but the radio is sending it nothing: repairing."
                repair_controller
                last_repair_time=$now
                last_tx=""
                silent_since=$now
                sleep "$CHECK_SECONDS"
                continue
            fi
            silent_since=$now
        fi

        sleep "$((CHECK_SECONDS * CONNECTED_TICKS))"
        continue
    fi

    if [ "$connected" -eq 1 ]; then
        echo "Lost $SPEAKER_MAC, waiting for it to come back."
        connected=0
    fi

    if [ "$tick" -ge "$next_attempt" ]; then
        request_connect
        if [ "$failures" -eq 0 ]; then
            echo "Asking $SPEAKER_MAC to connect."
        fi
        calm=0
        if [ "$failures" -lt "${#BACKOFF_TICKS[@]}" ]; then
            failures=$((failures + 1))
        else
            calm=1
        fi
        next_attempt=$((tick + BACKOFF_TICKS[failures - 1]))
        if [ "$calm" -eq 1 ] && [ "$shared" -eq 1 ]; then
            next_attempt=$((tick + SHARED_CALM_TICKS))
        fi
        if [ "$calm" -eq 1 ] && [ "$warned_calm" -eq 0 ]; then
            if [ "$shared" -eq 1 ]; then
                echo "$SPEAKER_MAC still not reachable, keeping one attempt every five minutes: this controller shares the Wi-Fi's radio."
            else
                echo "$SPEAKER_MAC still not reachable, keeping one attempt per minute."
            fi
            warned_calm=1
        fi

        # Once per burst: a wedged radio otherwise leaves nothing in the journal to go on.
        if [ "$connect_reported" -eq 0 ] && [ -n "$CONNECT_OUT" ]; then
            case "$CONNECT_OUT" in
                *"Connection successful"*) ;;
                *)
                    echo "Connect attempt answered: $(printf '%s' "$CONNECT_OUT" \
                        | tr -d '\r' | tr '\n' ' ' | cut -c1-140)"
                    connect_reported=1
                    ;;
            esac
        fi

        # Failing connects mean a jammed radio, not a speaker problem: reset it, at most every REPAIR_SECONDS.
        # The count is read against the level from when the radio worked: a jam whose timeouts stop is still a jam.
        marks=$(hci_stuck_marks)
        if [ $(( $(date +%s) - last_repair_time )) -ge "$REPAIR_SECONDS" ] \
                && { connect_refused \
                     || { [ "$failures" -ge "$REPAIR_AFTER_FAILURES" ] \
                          && [ "$marks" -gt "$healthy_marks" ]; }; }; then
            repair_controller
            last_repair_time=$(date +%s)
            healthy_marks=$(hci_stuck_marks)
            failures=0
            warned_calm=0
            connect_reported=0
            next_attempt=$((tick + 2))
        fi
    fi

    sleep "$CHECK_SECONDS"
done
