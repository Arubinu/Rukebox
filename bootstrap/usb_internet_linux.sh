#!/bin/bash
set -eu

MAC="02:1a:11:00:01:01"
HOST_ADDR="192.168.77.1/24"
SUBNET="192.168.77.0/24"
TAG="rukebox-usb"
STATE="/run/rukebox-usb-internet.state"

if [ "$(id -u)" -ne 0 ]; then
    exec sudo bash "$0" "$@"
fi

REMOVE=0
case "${1:-}" in
    --remove|-r) REMOVE=1 ;;
    "") ;;
    *) echo "Usage: $0 [--remove]"; exit 2 ;;
esac

IF=""
for dev in /sys/class/net/*; do
    if [ "$(cat "$dev/address" 2>/dev/null)" = "$MAC" ]; then
        IF="$(basename "$dev")"
        break
    fi
done

have() { command -v "$1" >/dev/null 2>&1; }

if have iptables; then
    FW=iptables
elif have nft; then
    FW=nft
else
    echo "Neither iptables nor nft is available: cannot share the connection."
    exit 1
fi

remove_rules() {
    if [ "$FW" = iptables ]; then
        iptables -t nat -S POSTROUTING | grep -- "--comment $TAG" | sed 's/^-A /-D /' |
            while read -r rule; do eval "iptables -t nat $rule"; done
        iptables -S FORWARD | grep -- "--comment $TAG" | sed 's/^-A /-D /' |
            while read -r rule; do eval "iptables $rule"; done
    else
        nft delete table ip rukebox_usb 2>/dev/null || true
    fi
}

if [ "$REMOVE" = 1 ]; then
    remove_rules
    if [ -f "$STATE" ]; then
        # shellcheck disable=SC1090
        . "$STATE"
        sysctl -qw "net.ipv4.ip_forward=${PREV_FORWARD:-0}"
        if [ -n "${IFACE:-}" ] && [ -d "/sys/class/net/$IFACE" ]; then
            ip addr del "$HOST_ADDR" dev "$IFACE" 2>/dev/null || true
            [ "${NM_RELEASED:-0}" = 1 ] && nmcli device set "$IFACE" managed yes 2>/dev/null || true
        fi
        rm -f "$STATE"
    fi
    echo "Done: the Rukebox no longer reaches the Internet through this computer."
    exit 0
fi

if [ -z "$IF" ]; then
    echo "The Rukebox's Internet card ($MAC) is not there."
    echo "Plug the cable into the Pi's USB data port; if the Pi runs an older"
    echo "version, update it first (bootstrap/push_update.sh)."
    exit 1
fi

OUT_IF="$(ip route show default 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "dev") {print $(i+1); exit}}')"
if [ -z "$OUT_IF" ]; then
    echo "This computer has no Internet connection (no default route)."
    exit 1
fi
if [ "$OUT_IF" = "$IF" ]; then
    echo "The default route goes through the Rukebox card itself - refusing."
    exit 1
fi

if [ ! -f "$STATE" ]; then
    {
        echo "PREV_FORWARD=$(sysctl -n net.ipv4.ip_forward)"
        echo "IFACE=$IF"
        NM=0
        if have nmcli && nmcli -t -f DEVICE,STATE device 2>/dev/null | grep -q "^$IF:" \
                && ! nmcli -t -f DEVICE,STATE device 2>/dev/null | grep -q "^$IF:unmanaged"; then
            NM=1
        fi
        echo "NM_RELEASED=$NM"
    } > "$STATE"
fi
# shellcheck disable=SC1090
. "$STATE"

[ "${NM_RELEASED:-0}" = 1 ] && nmcli device set "$IF" managed no 2>/dev/null || true
ip link set "$IF" up
ip addr show dev "$IF" | grep -q "inet ${HOST_ADDR%/*}/" || ip addr add "$HOST_ADDR" dev "$IF"
sysctl -qw net.ipv4.ip_forward=1

remove_rules
if [ "$FW" = iptables ]; then
    iptables -t nat -A POSTROUTING -s "$SUBNET" ! -o "$IF" -j MASQUERADE -m comment --comment "$TAG"
    if iptables -S FORWARD | head -1 | grep -q -- "-P FORWARD DROP"; then
        iptables -I FORWARD -i "$IF" -j ACCEPT -m comment --comment "$TAG"
        iptables -I FORWARD -o "$IF" -m state --state RELATED,ESTABLISHED -j ACCEPT -m comment --comment "$TAG"
    fi
else
    nft add table ip rukebox_usb
    nft add chain ip rukebox_usb post '{ type nat hook postrouting priority 100 ; }'
    nft add rule ip rukebox_usb post ip saddr "$SUBNET" oifname != "$IF" masquerade
fi

echo "Done: the Rukebox reaches the Internet through $OUT_IF (card $IF)."
echo "  Check from the Pi : ssh pi@169.254.7.7 'ping -c 2 1.1.1.1'"
echo "  Undo              : bash $0 --remove   (a reboot undoes it too)"
