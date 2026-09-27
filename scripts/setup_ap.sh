#!/bin/bash
set -euo pipefail

SSID="${1:-Rukebox-Admin}"
PASSPHRASE="${2:-}"

PROJECT_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../src" 2>/dev/null && pwd || echo "")"

if [ -n "$PASSPHRASE" ] && [ "${#PASSPHRASE}" -lt 8 ]; then
    echo "The password must be at least 8 characters (or omit it entirely for an open network)." >&2
    exit 1
fi

if [ "$EUID" -ne 0 ]; then
    echo "Run this script with sudo." >&2
    exit 1
fi

if systemctl is-active --quiet NetworkManager; then
    echo "== NetworkManager detected: creating the hotspot via nmcli =="

    if ! ip link show uap0 > /dev/null 2>&1; then
        echo "Interface uap0 doesn't exist yet: run first:" >&2
        echo "  sudo systemctl enable --now create-uap0.service" >&2
        exit 1
    fi

    CONN_NAME="rukebox-ap"
    nmcli connection delete "$CONN_NAME" > /dev/null 2>&1 || true

    # Created, then activated explicitly: "nmcli device wifi hotspot" fails on
    # brcmfmac (raspberrypi/linux#7247).
    nmcli connection add type wifi ifname uap0 con-name "$CONN_NAME" \
        autoconnect yes ssid "$SSID" -- \
        802-11-wireless.mode ap ipv4.method shared ipv6.method ignore \
        802-11-wireless.powersave 2
    if [ -n "$PASSPHRASE" ]; then
        nmcli connection modify "$CONN_NAME" \
            wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PASSPHRASE"
    else
        echo "WARNING: creating an OPEN access point (no password)." >&2
    fi
    nmcli connection up "$CONN_NAME" ifname uap0
    echo "Connection '$CONN_NAME' active, and set to start automatically at boot."

    AP_IP=$(nmcli -g IP4.ADDRESS device show uap0 2>/dev/null | head -1 | cut -d/ -f1)
    PORTAL_CONF=/etc/NetworkManager/dnsmasq-shared.d/rukebox-captive-portal.conf
    if [ -n "$AP_IP" ] && [ -f "$PROJECT_SRC/captive_portal.py" ]; then
        mkdir -p /etc/NetworkManager/dnsmasq-shared.d
        python3 "$PROJECT_SRC/captive_portal.py" dnsmasq "$AP_IP" > "$PORTAL_CONF"
        echo "Captive portal DNS entries written to $PORTAL_CONF (pointing at $AP_IP)."
    fi
    echo ""
    echo "Access point '$SSID' active on uap0."
    echo "Pi's IP address on this network: usually 10.42.0.1 (check with: nmcli device show uap0 | grep IP4.ADDRESS)"

else
    echo "== No active NetworkManager: installing hostapd + dnsmasq =="
    echo "WARNING: in this configuration, the Wi-Fi client toggle"
    echo "(scripts/setup_home_wifi.sh) is not handled automatically -"
    echo "you'll need to configure wpa_supplicant on wlan0 manually."
    apt-get update
    apt-get install -y hostapd dnsmasq

    systemctl unmask hostapd
    systemctl stop hostapd dnsmasq 2>/dev/null || true

    if ! ip link show uap0 > /dev/null 2>&1; then
        echo "Interface uap0 doesn't exist yet: run first:" >&2
        echo "  sudo systemctl enable --now create-uap0.service" >&2
        exit 1
    fi

    if ! grep -q "^interface uap0" /etc/dhcpcd.conf 2>/dev/null; then
        cat >> /etc/dhcpcd.conf <<EOF

interface uap0
    static ip_address=192.168.4.1/24
    nohook wpa_supplicant
EOF
    fi

    cat > /etc/dnsmasq.d/rukebox-ap.conf <<EOF
interface=uap0
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h
EOF

    cat > /etc/hostapd/hostapd.conf <<EOF
interface=uap0
driver=nl80211
ssid=${SSID}
hw_mode=g
channel=7
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
EOF
    if [ -n "$PASSPHRASE" ]; then
        cat >> /etc/hostapd/hostapd.conf <<EOF
wpa=2
wpa_passphrase=${PASSPHRASE}
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
EOF
    else
        echo "WARNING: creating an OPEN access point (no password)." >&2
    fi

    sed -i 's|#\?DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd

    systemctl enable hostapd dnsmasq
    systemctl restart dhcpcd
    systemctl restart hostapd dnsmasq

    echo ""
    echo "Access point '$SSID' active on uap0 (192.168.4.1)"
fi

# shellcheck disable=SC1091
[ -f /etc/rukebox/rukebox.env ] && source /etc/rukebox/rukebox.env
WEB_PORT="${WEB_PORT:-80}"
echo ""
if [ -n "$PASSPHRASE" ]; then
    echo "Connect your phone ONCE to the '$SSID' network (password required)."
else
    echo "Connect your phone to the '$SSID' network (open, no password)."
fi
echo "It will then reconnect automatically, like any known Wi-Fi network."
if [ "$WEB_PORT" = "80" ]; then
    echo "Web interface: http://<Pi's IP shown above>  - or simply accept the"
    echo "\"sign in to the network\" prompt your phone shows when it connects."
else
    echo "Web interface: http://<Pi's IP shown above>:${WEB_PORT}"
fi
