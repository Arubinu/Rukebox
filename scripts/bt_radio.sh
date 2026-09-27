#!/bin/bash
# Clears the Bluetooth radio's soft block (BlueZ cannot: a blocked switch makes
# "power on" fail). Some images come up blocked; run this as root.

set -u

for entry in /sys/class/rfkill/rfkill*; do
    [ "$(cat "$entry/type" 2>/dev/null)" = "bluetooth" ] || continue
    [ "$(cat "$entry/soft" 2>/dev/null)" = "1" ] || continue
    if echo 0 > "$entry/soft" 2>/dev/null; then
        echo "unblocked $(cat "$entry/name" 2>/dev/null)"
    else
        echo "could not unblock $(cat "$entry/name" 2>/dev/null): run me as root" >&2
    fi
done
