#!/bin/bash
set -euo pipefail

SDK_DIR="${FLIC_SDK_DIR:-/opt/fliclib-linux-hci}"
FLICD_DB="${FLICD_DB:-/var/lib/rukebox/flic.sqlite3}"
HCI_DEVICE="${FLIC_HCI_DEVICE:-hci0}"

hci_for_address() {
    hciconfig 2>/dev/null | awk -v mac="$(echo "$1" | tr 'a-f' 'A-F')" '
        /^hci[0-9]+:/ { dev = $1; sub(":", "", dev) }
        /BD Address:/ { if (toupper($3) == mac) print dev }' | head -1
}

# An address is given because hciN can change between boots (a dongle may come up before the chip).
if [[ "$HCI_DEVICE" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
    ADDRESS="$HCI_DEVICE"
    HCI_DEVICE=""
    for _ in $(seq 1 30); do
        HCI_DEVICE="$(hci_for_address "$ADDRESS")"
        [ -n "$HCI_DEVICE" ] && break
        sleep 1
    done
    if [ -z "$HCI_DEVICE" ]; then
        # A replaced dongle has another address: take the one USB controller, never a guess between several.
        USB_DEVICES="$(hciconfig 2>/dev/null | awk '/^hci[0-9]+:/ { dev = $1; sub(":", "", dev) }
            /Bus: USB/ { print dev }')"
        SPEAKER_DEVICE=""
        if [[ "${SPEAKER_BT_ADAPTER:-}" =~ ^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$ ]]; then
            SPEAKER_DEVICE="$(hci_for_address "$SPEAKER_BT_ADAPTER")"
        fi
        # Only when the speaker is clearly elsewhere: flicd would take its controller from it.
        SPEAKER_ELSEWHERE=0
        if [ "${AUDIO_OUTPUT:-bluetooth}" != "bluetooth" ]; then
            SPEAKER_ELSEWHERE=1
        elif [ -n "$SPEAKER_DEVICE" ] && ! printf '%s\n' "$USB_DEVICES" | grep -qx "$SPEAKER_DEVICE"; then
            SPEAKER_ELSEWHERE=1
        fi
        if [ "$SPEAKER_ELSEWHERE" -eq 1 ] && [ "$(printf '%s\n' "$USB_DEVICES" | grep -c .)" -eq 1 ]; then
            HCI_DEVICE="$USB_DEVICES"
            echo "flicd: no controller with address $ADDRESS, using the USB one ($HCI_DEVICE)" >&2
        else
            echo "flicd: no Bluetooth controller with address $ADDRESS" >&2
            exit 1
        fi
    fi
fi

if [ -z "${FLICD_BIN:-}" ] || [ ! -x "$FLICD_BIN" ]; then
    case "$(uname -m)" in
        aarch64|arm64) ARCH=aarch64 ;;
        armv6l|armv7l) ARCH=armv6l ;;
        x86_64) ARCH=x86_64 ;;
        i?86) ARCH=i386 ;;
        *) ARCH="$(uname -m)" ;;
    esac
    FLICD_BIN="$SDK_DIR/bin/$ARCH/flicd"
fi
if [ ! -x "$FLICD_BIN" ]; then
    echo "flicd: $FLICD_BIN not found - install the Flic SDK first" >&2
    exit 1
fi

echo "flicd: $FLICD_BIN on $HCI_DEVICE" >&2
exec "$FLICD_BIN" -f "$FLICD_DB" -h "$HCI_DEVICE" -w
