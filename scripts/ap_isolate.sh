#!/bin/sh
# NetworkManager dispatcher hook: the access point is never a gateway.
# Installed as /etc/NetworkManager/dispatcher.d/90-rukebox-ap-isolate; called as "<interface> <action>".
AP_IF="uap0"
# shellcheck disable=SC1091
[ -r /etc/rukebox/rukebox.env ] && . /etc/rukebox/rukebox.env && AP_IF="${AP_INTERFACE:-uap0}"
[ "$1" = "$AP_IF" ] || exit 0
[ "$2" = "up" ] || exit 0
command -v nft >/dev/null 2>&1 || exit 0
nft delete table inet rukebox_ap 2>/dev/null
# "shared" mode turns forwarding and NAT on for the hotspot; a drop here wins over NM's own accept.
nft add table inet rukebox_ap
nft add chain inet rukebox_ap forward '{ type filter hook forward priority -10 ; policy accept ; }'
nft add rule inet rukebox_ap forward iifname "$AP_IF" drop
nft add rule inet rukebox_ap forward oifname "$AP_IF" drop
