#!/bin/bash
set -u

[ -f /etc/rukebox/rukebox.env ] && . /etc/rukebox/rukebox.env
MODE="${ACT_LED:-default}"
STATE=/run/rukebox-act-led.default

LED=""
for name in ACT led0; do
    if [ -d "/sys/class/leds/$name" ]; then
        LED="/sys/class/leds/$name"
        break
    fi
done
if [ -z "$LED" ]; then
    echo "No activity LED found (not a Raspberry Pi?): nothing to do."
    exit 0
fi

current=$(grep -o '\[[^]]*\]' "$LED/trigger" | tr -d '[]')
if [ ! -s "$STATE" ] && [ -n "$current" ] && [ "$current" != "none" ]; then
    echo "$current" > "$STATE"
fi

case "$MODE" in
    off)
        echo none > "$LED/trigger"
        echo 0 > "$LED/brightness"
        echo "Activity LED: off"
        ;;
    *)
        default=$(cat "$STATE" 2>/dev/null)
        if [ -z "$default" ]; then
            for candidate in actpwr mmc0 default-on; do
                if grep -qw "$candidate" "$LED/trigger"; then
                    default=$candidate
                    break
                fi
            done
        fi
        [ -n "$default" ] && echo "$default" > "$LED/trigger"
        echo "Activity LED: original behaviour ($default)"
        ;;
esac
exit 0
