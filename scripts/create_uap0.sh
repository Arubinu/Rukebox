#!/bin/bash
set -euo pipefail

if ! iw dev wlan0 info > /dev/null 2>&1; then
    echo "wlan0 not found, aborting (no Wi-Fi chip detected?)" >&2
    exit 1
fi

if ! iw dev | grep -q "Interface uap0"; then
    iw dev wlan0 interface add uap0 type __ap
fi

# Often still soft-blocked this early: NetworkManager brings uap0 up when it activates the AP.
if ip link set uap0 up 2>/dev/null; then
    echo "Interface uap0 ready (virtual AP, wlan0 stays free for client mode)."
else
    echo "Interface uap0 created; NetworkManager will bring it up (radio not unblocked yet)."
fi
