#!/bin/bash
set -u

CONFIG_FILE="/etc/rukebox/rukebox.env"

CHECK_SECONDS=3
CONNECTED_TICKS=10
SLOW_TICKS=20
BACKOFF_TICKS=(1 2 3 5 10 20)
CALL_TIMEOUT=10
# A connect on a jammed controller blocks until bluetoothd gives up, which is
# longer than a plain query: 10s used to kill the attempt before it could
# either succeed or say why (measured: 1s when healthy, over 10s when jammed).
CONNECT_TIMEOUT=30
REPAIR_AFTER_FAILURES=3
REPAIR_TICKS=100

SPEAKER_MAC=""
ADAPTER=""

reload_config() {
    if [ -f "$CONFIG_FILE" ]; then
        # shellcheck disable=SC1090
        . "$CONFIG_FILE"
    fi
    SPEAKER_MAC="${SPEAKER_MAC:-}"
    ADAPTER="${SPEAKER_BT_ADAPTER:-}"
}

adapter_index() {
    case "$ADAPTER" in
        hci*) echo "${ADAPTER#hci}" ;;
        ??:??:??:??:??:??)
            hciconfig 2>/dev/null | awk -v mac="$(echo "$ADAPTER" | tr 'a-f' 'A-F')" '
                /^hci[0-9]+:/ { dev = $1; sub(":", "", dev); sub("hci", "", dev) }
                /BD Address:/ { if (toupper($3) == mac) print dev }' | head -1 ;;
    esac
}

# The controller to work on: the configured one, or BlueZ's default - which is
# the one it connects the speaker through when no adapter is named (the usual
# case, and the one where an empty SPEAKER_BT_ADAPTER left the count below at
# zero, so the repair never ran).
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

# btmgmt hangs with stdin on /dev/null (what systemd gives): it needs a pipe.
# Always aimed at the speaker's controller: the default one may belong to
# flicd.
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
        # No settings line at all: the controller did not answer. Saying
        # "not connectable" here would send us turning a setting on that is
        # not the problem - the radio needs repairing instead (see below).
        echo "The Bluetooth controller did not answer btmgmt."
        return 1
    fi
    case " $settings " in
        *" connectable "*) return 0 ;;
    esac
    echo "Adapter was not connectable (settings: ${settings}), enabling it so the speaker can reconnect on its own."
    btmgmt_cmd connectable on >/dev/null 2>&1 || true
}

# The kernel logs one "tx timeout" line for every HCI command the controller
# never answered. A radio whose queue is jammed by a half-open connection - a
# link that was killed, then a `command 0x041f tx timeout` every twenty
# seconds for ever - still answers btmgmt, still says "UP RUNNING", and
# refuses every new operation with org.bluez.Error.InProgress or
# br-connection-busy. Nothing but a reset clears it, so that is what the
# count below is for. Measured on the owner's Pi, twice in one day.
#
# Only the CONTROLLER's own failures count: "link tx timeout" (the speaker
# stopped answering) is a speaker that is off or out of range, and resetting
# the radio for that would be pointless - measured side by side on that Pi:
# 67 "command ... tx timeout" against 4 "link tx timeout".
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

# The sequence that brought the radio back by hand, twice: bluetoothd stopped,
# the controller taken down and up, bluetoothd restarted. A plain down/up
# leaves it DOWN, and btmgmt then answers "Cannot allocate memory (12)".
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

# The controller's own answer when its queue is jammed - nothing is connected,
# yet it refuses every attempt. Measured on the owner's Pi, both strings, in the
# very state `hci_stuck_marks` counts. `br-connection-refused` is NOT one of
# them: that one means the speaker is connected elsewhere (a phone).
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
warned_calm=0
connect_reported=0
# The kernel's timeout count as it was when the radio last worked. Any growth
# since then means the controller jammed in between - see the repair below.
healthy_marks=0
last_repair_tick=-$((REPAIR_TICKS * 2))
healthy_marks=$(hci_stuck_marks)

while :; do
    tick=$((tick + 1))

    if [ $((tick % SLOW_TICKS)) -eq 1 ]; then
        reload_config
        ensure_connectable
    fi

    if [ -z "$SPEAKER_MAC" ] || [ "$SPEAKER_MAC" = "XX:XX:XX:XX:XX:XX" ]; then
        if [ "$warned_unconfigured" -eq 0 ]; then
            echo "No speaker configured (speaker_mac in /etc/rukebox/rukebox.yaml) - waiting for one."
            warned_unconfigured=1
        fi
        sleep "$CHECK_SECONDS"
        continue
    fi

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
        if [ "$failures" -lt "${#BACKOFF_TICKS[@]}" ]; then
            failures=$((failures + 1))
        elif [ "$warned_calm" -eq 0 ]; then
            echo "$SPEAKER_MAC still not reachable, keeping one attempt per minute."
            warned_calm=1
        fi
        next_attempt=$((tick + BACKOFF_TICKS[failures - 1]))

        # Why the attempt failed, once per burst: a wedged radio otherwise
        # leaves nothing at all in the journal to go on.
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

        # Connections that keep failing are not a speaker problem any more: the
        # radio is jammed (see hci_stuck_marks above). Either the controller
        # says so itself - which is what connect_refused matches - or the kernel
        # has timed out at least once since the radio last worked. Reset it, at
        # most every REPAIR_TICKS.
        #
        # The count is compared with the level recorded while the radio worked,
        # not with its previous reading: a jam whose timeouts STOP coming (the
        # kernel gives up its own retries) is still a jam, and waiting for the
        # count to grow left the speaker off the air for good - measured on the
        # owner's Pi, 20:12, where 97 -> 102 then nothing, and no repair.
        marks=$(hci_stuck_marks)
        if [ $((tick - last_repair_tick)) -ge "$REPAIR_TICKS" ] \
                && { connect_refused \
                     || { [ "$failures" -ge "$REPAIR_AFTER_FAILURES" ] \
                          && [ "$marks" -gt "$healthy_marks" ]; }; }; then
            repair_controller
            last_repair_tick=$tick
            healthy_marks=$(hci_stuck_marks)
            failures=0
            warned_calm=0
            connect_reported=0
            next_attempt=$((tick + 2))
        fi
    fi

    sleep "$CHECK_SECONDS"
done
