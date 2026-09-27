#!/bin/bash
set -eu

MAC="02:1a:11:00:01:01"
HOST_IP="192.168.77.1"
SUBNET="192.168.77.0/24"
ANCHOR="com.apple/rukebox"
STATE="/tmp/rukebox-usb-internet.state"

if [ "$(id -u)" -ne 0 ]; then
    exec sudo bash "$0" "$@"
fi

REMOVE=0
case "${1:-}" in
    --remove|-r) REMOVE=1 ;;
    "") ;;
    *) echo "Usage: $0 [--remove]"; exit 2 ;;
esac

IF="$(ifconfig | awk -v mac="$MAC" '/^[a-z]/ {name = $1; sub(/:$/, "", name)} $1 == "ether" && $2 == mac {print name; exit}')"

if [ "$REMOVE" = 1 ]; then
    pfctl -a "$ANCHOR" -F all 2>/dev/null || true
    if [ -f "$STATE" ]; then
        # shellcheck disable=SC1090
        . "$STATE"
        [ -n "${PF_TOKEN:-}" ] && pfctl -X "$PF_TOKEN" 2>/dev/null || true
        sysctl -w "net.inet.ip.forwarding=${PREV_FORWARD:-0}" >/dev/null
        if [ -n "${IFACE:-}" ]; then
            ifconfig "$IFACE" inet "$HOST_IP" -alias 2>/dev/null || true
        fi
        rm -f "$STATE"
    fi
    echo "Done: the Rukebox no longer reaches the Internet through this Mac."
    exit 0
fi

if [ -z "$IF" ]; then
    echo "The Rukebox's Internet card ($MAC) is not there."
    echo "Plug the cable into the Pi's USB data port; if the Pi runs an older"
    echo "version, update it first (bootstrap/push_update.sh)."
    exit 1
fi

OUT_IF="$(route -n get default 2>/dev/null | awk '/interface:/ {print $2; exit}')"
if [ -z "$OUT_IF" ]; then
    echo "This Mac has no Internet connection (no default route)."
    exit 1
fi
if [ "$OUT_IF" = "$IF" ]; then
    echo "The default route goes through the Rukebox card itself - refusing."
    exit 1
fi

if [ ! -f "$STATE" ]; then
    echo "PREV_FORWARD=$(sysctl -n net.inet.ip.forwarding)" > "$STATE"
    echo "IFACE=$IF" >> "$STATE"
fi
# shellcheck disable=SC1090
. "$STATE"

ifconfig "$IF" | grep -q "inet $HOST_IP " || ifconfig "$IF" inet "$HOST_IP" netmask 255.255.255.0 alias
sysctl -w net.inet.ip.forwarding=1 >/dev/null

if ! pfctl -s nat 2>/dev/null | grep -q 'nat-anchor "com.apple/\*"'; then
    pfctl -f /etc/pf.conf 2>/dev/null
fi
echo "nat on $OUT_IF inet from $SUBNET to any -> ($OUT_IF)" | pfctl -a "$ANCHOR" -f - 2>/dev/null
if [ -z "${PF_TOKEN:-}" ]; then
    PF_TOKEN="$(pfctl -E 2>&1 | awk '/Token/ {print $NF}')"
    echo "PF_TOKEN=$PF_TOKEN" >> "$STATE"
fi

echo "Done: the Rukebox reaches the Internet through $OUT_IF (card $IF)."
echo "  Check from the Pi : ssh pi@169.254.7.7 'ping -c 2 1.1.1.1'"
echo "  Undo              : bash $0 --remove   (a reboot undoes it too)"
echo "  If the Mac changes connection (Wi-Fi <-> Ethernet), run this again."
