#!/bin/bash
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "Run this script with sudo." >&2
    exit 1
fi

if ! systemctl is-active --quiet NetworkManager; then
    echo "NetworkManager is not active on this system." >&2
    echo "This script only handles the NetworkManager path (see README," >&2
    echo "Personal Wi-Fi section, for manual wpa_supplicant configuration)." >&2
    exit 1
fi

read -rp "Personal network name (SSID): " ssid
read -rsp "Password: " passphrase
echo ""

echo "Connecting to '$ssid' via wlan0..."
nmcli device wifi connect "$ssid" password "$passphrase" ifname wlan0

CONN_NAME=$(nmcli -t -f NAME,DEVICE connection show --active | grep ':wlan0$' | cut -d: -f1 | head -1)
if [ -z "$CONN_NAME" ]; then
    echo "Failed: could not determine the connection profile created." >&2
    exit 1
fi

# powersave 2 = off: the radio is shared with Bluetooth audio and its wake-ups make the sound stutter.
nmcli connection modify "$CONN_NAME" connection.autoconnect yes \
    connection.interface-name wlan0 802-11-wireless.powersave 2

echo ""
echo "Connected. NetworkManager profile: '$CONN_NAME'"
echo ""
echo "The web interface needs to know this profile's name to be able to"
echo "toggle this connection (home_wifi_connection in rukebox.yaml):"
echo ""
echo "  $CONN_NAME"
echo ""

if [ -f /etc/rukebox/rukebox.yaml ]; then
    read -rp "Write this value to /etc/rukebox/rukebox.yaml now? (y/n) " confirm
    if [[ "$confirm" =~ ^[yY] ]]; then
        python3 /opt/rukebox/src/config_file.py set "HOME_WIFI_CONN_NAME=${CONN_NAME}"
        echo "Done. Restart the web service to apply:"
        echo "  sudo systemctl restart rukebox-web.service"
    fi
fi

echo ""
echo "Current IP on this network: $(nmcli -g IP4.ADDRESS device show wlan0 | head -1)"
echo "The access point (uap0) stays active in parallel, without interruption."
