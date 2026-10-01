#!/bin/bash
# NetworkManager does not come back from a connection that failed for missing secrets,
# and a headless Pi has no agent to answer the key prompt.
set -u

CONFIG_FILE="/etc/rukebox/rukebox.env"

CHECK_SECONDS=8
CONNECTED_TICKS=8
BACKOFF_TICKS=(1 2 3 5 10 20)
CALL_TIMEOUT=30

CONN=""
WANTED=0
AUTOCONNECT=0

reload_config() {
    if [ -f "$CONFIG_FILE" ]; then
        # shellcheck disable=SC1090
        . "$CONFIG_FILE"
    fi
    CONN="${HOME_WIFI_CONN_NAME:-}"
    # Both must agree: this enforces "Connection active", autoconnect allows it up at all.
    case "${HOME_WIFI_ENABLED:-true}" in
        1|true|True|yes|on) WANTED=1 ;;
        *) WANTED=0 ;;
    esac
    AUTOCONNECT=0
    if [ -n "$CONN" ]; then
        case "$(timeout 10 nmcli -g connection.autoconnect connection show "$CONN" 2>/dev/null)" in
            yes) AUTOCONNECT=1 ;;
        esac
    fi
}

is_up() {
    timeout 10 nmcli -t -f NAME,DEVICE,STATE connection show --active 2>/dev/null \
        | grep -qx "$CONN:wlan0:activated"
}

came_up() {
    timeout "$CALL_TIMEOUT" nmcli connection up "$CONN" >/dev/null 2>&1
}

tick=0
failures=0
next_attempt=0
up=0
warned_calm=0

while :; do
    tick=$((tick + 1))
    reload_config

    if [ -z "$CONN" ] || [ "$WANTED" -eq 0 ] || [ "$AUTOCONNECT" -eq 0 ]; then
        up=0
        failures=0
        next_attempt=0
        warned_calm=0
        sleep "$CHECK_SECONDS"
        continue
    fi

    if is_up; then
        if [ "$up" -eq 0 ]; then
            echo "Personal Wi-Fi: connected to '$CONN'."
            up=1
        fi
        failures=0
        warned_calm=0
        next_attempt=$((tick + CONNECTED_TICKS))
        sleep "$CHECK_SECONDS"
        continue
    fi

    if [ "$up" -eq 1 ]; then
        echo "Personal Wi-Fi: '$CONN' went down, taking it back."
        up=0
        # Retried at once: this connection is how the page and SSH are reached from the house.
        next_attempt=$tick
        failures=0
    fi

    if [ "$tick" -ge "$next_attempt" ]; then
        if [ "$failures" -eq 0 ]; then
            echo "Asking NetworkManager to bring '$CONN' up."
        fi
        if came_up; then
            failures=0
            next_attempt=$((tick + 1))
        else
            if [ "$failures" -lt "${#BACKOFF_TICKS[@]}" ]; then
                failures=$((failures + 1))
            elif [ "$warned_calm" -eq 0 ]; then
                echo "'$CONN' still refuses to come up, keeping one attempt per minute."
                warned_calm=1
            fi
            next_attempt=$((tick + BACKOFF_TICKS[failures - 1]))
        fi
    fi

    sleep "$CHECK_SECONDS"
done
