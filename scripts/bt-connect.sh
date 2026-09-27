#!/bin/bash
set -u

CONFIG_FILE="/etc/rukebox/rukebox.env"

CHECK_SECONDS=3
CONNECTED_TICKS=10
SLOW_TICKS=20
BACKOFF_TICKS=(1 2 3 5 10 20)
CALL_TIMEOUT=10

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
    case " $settings " in
        *" connectable "*) return 0 ;;
    esac
    echo "Adapter was not connectable (settings: ${settings:-unknown}), enabling it so the speaker can reconnect on its own."
    btmgmt_cmd connectable on >/dev/null 2>&1 || true
}

is_connected() {
    timeout "$CALL_TIMEOUT" bluetoothctl info "$SPEAKER_MAC" 2>/dev/null \
        | grep -q "Connected: yes"
}

request_connect() {
    if [ -n "$ADAPTER" ]; then
        printf 'select %s\nconnect %s\n' "$ADAPTER" "$SPEAKER_MAC"
    else
        printf 'connect %s\n' "$SPEAKER_MAC"
    fi | timeout "$CALL_TIMEOUT" bluetoothctl >/dev/null 2>&1 || true
}

tick=0
failures=0
next_attempt=0
connected=0
warned_unconfigured=0
warned_calm=0

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
    fi

    sleep "$CHECK_SECONDS"
done
