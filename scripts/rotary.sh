#!/bin/bash
# The rotary encoder: its overlays in config.txt (read at the next boot) and its listener's service.
set -u

log() { echo "[rotary] $*"; }

ENV_FILE="${RUKEBOX_ENV_FILE:-/etc/rukebox/rukebox.env}"
CONFIG_TXT="${RUKEBOX_CONFIG_TXT:-/boot/firmware/config.txt}"
[ -f "$CONFIG_TXT" ] || CONFIG_TXT="/boot/config.txt"
BEGIN="# rukebox: rotary encoder (begin)"
END="# rukebox: rotary encoder (end)"
# The push's key code: the one src/rotary.py listens for.
PUSH_KEYCODE=203

get() { sed -n "s/^$1='\(.*\)'$/\1/p" "$ENV_FILE" 2>/dev/null | tail -1; }
pin_ok() { [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 2 ] && [ "$1" -le 27 ]; }

ON="$(get BUTTONS_ROTARY_ENABLED)"
PIN_A="$(get BUTTONS_ROTARY_PIN_A)"
PIN_B="$(get BUTTONS_ROTARY_PIN_B)"
PIN_PUSH="$(get BUTTONS_ROTARY_PIN_PUSH)"

WANT=""
if [ "$ON" = "true" ]; then
    if pin_ok "$PIN_A" && pin_ok "$PIN_B" && [ "$PIN_A" != "$PIN_B" ]; then
        WANT="dtoverlay=rotary-encoder,pin_a=$PIN_A,pin_b=$PIN_B,relative_axis=1"
        if pin_ok "$PIN_PUSH" && [ "$PIN_PUSH" != "$PIN_A" ] && [ "$PIN_PUSH" != "$PIN_B" ]; then
            WANT="$WANT
dtoverlay=gpio-key,gpio=$PIN_PUSH,active_low=1,gpio_pull=up,keycode=$PUSH_KEYCODE"
        fi
    else
        log "pins $PIN_A / $PIN_B are not two distinct GPIO pins (2-27): the encoder stays off"
        ON="false"
    fi
fi

if [ -f "$CONFIG_TXT" ]; then
    HAVE="$(sed -n "/^$BEGIN\$/,/^$END\$/{/^# rukebox/d;/^\[all\]\$/d;p}" "$CONFIG_TXT")"
    if [ "$HAVE" != "$WANT" ]; then
        sed -i "/^$BEGIN\$/,/^$END\$/d" "$CONFIG_TXT"
        if [ -n "$WANT" ]; then
            printf '%s\n[all]\n%s\n%s\n' "$BEGIN" "$WANT" "$END" >> "$CONFIG_TXT"
        fi
        log "config.txt updated (takes effect after a reboot)"
    fi
else
    log "no config.txt here: nothing to write"
fi

if command -v systemctl >/dev/null 2>&1; then
    if [ "$ON" = "true" ]; then
        systemctl enable --now rukebox-rotary.service >/dev/null 2>&1 || true
    else
        systemctl disable --now rukebox-rotary.service >/dev/null 2>&1 || true
    fi
fi
exit 0
