#!/bin/bash
set -uo pipefail

CONFIG_FILE="/etc/rukebox/rukebox.yaml"
CONFIG_SET="/usr/bin/python3 /opt/rukebox/src/config_file.py set"
BT_CLOCK_PY="/opt/rukebox/src/bt_clock.py"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "Config not found: $CONFIG_FILE (run install.sh first)" >&2
    exit 1
fi

pair_and_test_device() {
    echo ""
    echo "== Scanning for nearby Bluetooth devices (10s) =="
    echo "Put your phone in discoverable mode now."
    bluetoothctl --timeout 10 scan on > /tmp/bt_scan.log 2>&1
    echo ""
    echo "Devices found:"
    bluetoothctl devices | nl -w2 -s') '
    echo ""
    read -rp "MAC address of the device to use (e.g. AA:BB:CC:DD:EE:FF), or 'q' to quit: " mac
    if [ "$mac" = "q" ]; then
        return 2
    fi

    echo "Pairing with $mac..."
    bluetoothctl pair "$mac"
    bluetoothctl trust "$mac"

    echo ""
    echo "Testing time read (Current Time Service)..."
    result=$(python3 "$BT_CLOCK_PY" "$mac" 15 2>/tmp/bt_clock_test.log)
    status=$?

    if [ $status -eq 0 ]; then
        echo ""
        echo "OK: time recovered successfully -> $result"
        echo "This device does expose the Current Time service."
        echo "$mac" > /tmp/bt_clock_mac_ok
        return 0
    else
        echo ""
        echo "FAILED: could not recover the time from this device."
        echo "Most common cause: the device (often an iPhone) doesn't"
        echo "expose the Current Time service for reading, even once paired."
        echo "Technical detail: $(tail -1 /tmp/bt_clock_test.log 2>/dev/null)"
        return 1
    fi
}

echo "=== Setting up the Bluetooth clock fallback ==="

while true; do
    pair_and_test_device
    outcome=$?

    if [ $outcome -eq 0 ]; then
        mac=$(cat /tmp/bt_clock_mac_ok)
        $CONFIG_SET "BT_CLOCK_MAC=${mac}" "BT_CLOCK_ENABLED=true"
        echo ""
        echo "Configured in $CONFIG_FILE: BT_CLOCK_ENABLED=true, BT_CLOCK_MAC=$mac"
        break
    elif [ $outcome -eq 2 ]; then
        $CONFIG_SET "BT_CLOCK_ENABLED=false"
        echo ""
        echo "Cancelled. BT_CLOCK_ENABLED=false: only the grace period will"
        echo "be used in the absence of an RTC (see CLOCK_SYNC_GRACE_SEC)."
        break
    else
        echo ""
        read -rp "Try another device? (y/n) " retry
        if [[ ! "$retry" =~ ^[yY] ]]; then
            $CONFIG_SET "BT_CLOCK_ENABLED=false"
            echo "BT_CLOCK_ENABLED=false: only the grace period will be used."
            break
        fi
    fi
done

rm -f /tmp/bt_clock_mac_ok /tmp/bt_clock_test.log /tmp/bt_scan.log
echo ""
echo "Done. Restart the daemon to apply: sudo systemctl restart rukebox-daemon.service"
