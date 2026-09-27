#!/bin/bash
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOT_DIR="${RUKEBOX_BOOT_DIR:-/boot/firmware}"
[ -d "$BOOT_DIR" ] || BOOT_DIR="/boot"
STATE_DIR="${RUKEBOX_FIRSTBOOT_DIR:-/var/lib/rukebox/firstboot}"
STATUS_FILE="${RUKEBOX_INSTALL_STATUS:-/run/rukebox-install/status.json}"
LOG_FILE="${RUKEBOX_INSTALL_LOG:-/var/log/rukebox-install.log}"
AUDIO_DIR="${RUKEBOX_AUDIO_DIR:-/home/pi/audio}"
STATUS_PORT="${RUKEBOX_INSTALL_PORT:-80}"
DRYRUN="${RUKEBOX_FIRSTBOOT_DRYRUN:-0}"
SETUP="$STATE_DIR/setup.json"
HOME_CONN="rukebox-home"
TOTAL_STEPS=6

mkdir -p "$STATE_DIR" "$(dirname "$STATUS_FILE")" "$(dirname "$LOG_FILE")"
chmod 700 "$STATE_DIR"
exec >> "$LOG_FILE" 2>&1
echo ""
echo "$(date) - Rukebox first-boot installation (pid $$)"

run() {
    if [ "$DRYRUN" = "1" ]; then echo "[dry-run] $*"; return 0; fi
    "$@"
}

status() {
    STEP="$1" KEY="$2" DETAIL="${3:-}" ERROR="${4:-}" TOTAL="$TOTAL_STEPS" \
    python3 - "$STATUS_FILE" <<'PY'
import json, os, sys, time
path = sys.argv[1]
data = {"step": int(os.environ["STEP"]), "total": int(os.environ["TOTAL"]),
        "key": os.environ["KEY"], "detail": os.environ["DETAIL"],
        "error": os.environ["ERROR"] or None, "at": time.time()}
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f)
os.replace(tmp, path)
PY
    echo "-- step $1/$TOTAL_STEPS: $2 ${3:-} ${4:+(error: $4)}"
}

done_step()  { touch "$STATE_DIR/done-$1"; }
is_done()    { [ -f "$STATE_DIR/done-$1" ]; }

setting() {
    python3 - "$SETUP" "$1" "${2:-}" <<'PY'
import json, sys
path, key, default = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    value = json.load(open(path))
    for part in key.split("."):
        value = value[part]
except (OSError, ValueError, KeyError, TypeError):
    value = default
if isinstance(value, bool):
    value = "yes" if value else "no"
print("" if value is None else value)
PY
}

has_internet() {
    timeout 5 bash -c 'cat < /dev/null > /dev/tcp/deb.debian.org/443' 2>/dev/null
}

STATUS_PID=""
start_status_server() {
    python3 "$PROJECT_ROOT/src/install_status.py" --port "$STATUS_PORT" \
        --status "$STATUS_FILE" --log "$LOG_FILE" --web "$PROJECT_ROOT/web" &
    STATUS_PID=$!
}
stop_status_server() {
    [ -n "$STATUS_PID" ] && kill "$STATUS_PID" 2>/dev/null
    STATUS_PID=""
}
trap stop_status_server EXIT

status 1 install.step_prepare
start_status_server

if ! is_done prepare; then
    if [ -f "$BOOT_DIR/rukebox-setup.json" ]; then
        if python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$BOOT_DIR/rukebox-setup.json"; then
            install -m 600 "$BOOT_DIR/rukebox-setup.json" "$SETUP"
            run rm -f "$BOOT_DIR/rukebox-setup.json"
        else
            status 1 install.step_prepare "" setup_unreadable
            echo "rukebox-setup.json is not valid JSON - nothing done."
            sleep infinity
        fi
    fi
    [ -f "$SETUP" ] || { status 1 install.step_prepare "" setup_missing; sleep infinity; }
    if id -u pi >/dev/null 2>&1; then
        run chown -R pi:pi /home/pi
    fi
    NAME="$(setting hostname)"
    if [[ "$NAME" =~ ^[A-Za-z0-9-]{1,63}$ ]]; then
        run hostnamectl set-hostname "$NAME"
        [ "$DRYRUN" = "1" ] || sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$NAME/" /etc/hosts
    fi
    ZONE="$(setting timezone)"
    if [ -n "$ZONE" ] && timedatectl list-timezones 2>/dev/null | grep -qx "$ZONE"; then
        run timedatectl set-timezone "$ZONE"
    fi
    done_step prepare
fi

AP_SSID="$(setting access_point.ssid Rukebox)"
AP_PASSWORD="$(setting access_point.password)"
COUNTRY="$(setting wifi_country)"
if ! is_done ap; then
    status 2 install.step_ap "$AP_SSID"
    if [[ "$COUNTRY" =~ ^[A-Za-z]{2}$ ]] && command -v raspi-config >/dev/null 2>&1; then
        run raspi-config nonint do_wifi_country "${COUNTRY^^}"
    fi
    run "$PROJECT_ROOT/scripts/create_uap0.sh" \
        && run "$PROJECT_ROOT/scripts/setup_ap.sh" "$AP_SSID" "$AP_PASSWORD" \
        || echo "WARNING: the access point could not be started - the USB cable still works."
    done_step ap
fi

flash_wifi_profiles() {
    nmcli -t -f NAME,TYPE,FILENAME connection show 2>/dev/null | while IFS=: read -r name type file; do
        [ "$type" = "802-11-wireless" ] || continue
        case "$name" in rukebox-ap|"$HOME_CONN") continue ;; esac
        [ "$(nmcli -g 802-11-wireless.mode connection show "$name" 2>/dev/null)" = "ap" ] && continue
        printf '%s\t%s\n' "$name" "$file"
    done
}

retire_netplan() {
    if [ -d /etc/cloud ]; then
        run mkdir -p /etc/cloud/cloud.cfg.d
        [ "$DRYRUN" = "1" ] || echo "network: {config: disabled}" > /etc/cloud/cloud.cfg.d/99-rukebox-no-network.cfg
    fi
    for f in /etc/netplan/*.yaml; do
        [ -f "$f" ] && run rm -f "$f"
    done
}

adopt_flash_wifi() {
    local name file
    IFS=$'\t' read -r name file < <(flash_wifi_profiles | head -n1)
    if [ -z "${name:-}" ]; then
        echo "No Wi-Fi from the flash to keep."
        return 1
    fi
    echo "Keeping the flash-time Wi-Fi '$name' ($file) as $HOME_CONN"
    run nmcli connection clone "$name" "$HOME_CONN" || return 1
    run nmcli connection modify "$HOME_CONN" connection.interface-name wlan0 \
        connection.autoconnect yes 802-11-wireless.powersave 2
    run nmcli connection delete "$name" || true
    retire_netplan
    run nmcli connection up "$HOME_CONN" ifname wlan0 || true
}

use_new_wifi() {
    local ssid password name file
    ssid="$(setting home_wifi.ssid)"
    password="$(setting home_wifi.password)"
    [ -n "$ssid" ] || { echo "No Wi-Fi name given."; return 1; }
    while IFS=$'\t' read -r name file; do
        [ -n "$name" ] && run nmcli connection delete "$name"
    done < <(flash_wifi_profiles)
    retire_netplan
    run nmcli connection delete "$HOME_CONN" >/dev/null 2>&1 || true
    run nmcli connection add type wifi ifname wlan0 con-name "$HOME_CONN" \
        autoconnect yes ssid "$ssid" -- connection.interface-name wlan0 \
        802-11-wireless.powersave 2 || return 1
    if [ -n "$password" ]; then
        run nmcli connection modify "$HOME_CONN" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$password"
    fi
    run nmcli connection up "$HOME_CONN" ifname wlan0 || true
}

if ! is_done internet; then
    MODE="$(setting internet flash)"
    case "$MODE" in
        flash) status 3 install.step_internet_wifi "" ; adopt_flash_wifi || echo "Falling back to waiting for any connection." ;;
        wifi)  status 3 install.step_internet_wifi "$(setting home_wifi.ssid)"; use_new_wifi || echo "Falling back to waiting for any connection." ;;
        usb|ethernet)
               adopt_flash_wifi || true ;;
    esac
    waited=0
    until [ "$DRYRUN" = "1" ] || has_internet; do
        case "$MODE" in
            usb)      status 3 install.step_internet_usb ;;
            ethernet) status 3 install.step_internet_ethernet ;;
            *)        status 3 install.step_internet_wait "$(setting home_wifi.ssid)" ;;
        esac
        sleep 5
        waited=$((waited + 5))
        if [ "$MODE" != "usb" ] && [ "$MODE" != "ethernet" ] && [ $((waited % 60)) -eq 0 ]; then
            nmcli connection up "$HOME_CONN" ifname wlan0 >/dev/null 2>&1 || true
        fi
    done
    done_step internet
fi

if ! is_done packages; then
    attempt=1
    while :; do
        status 4 install.step_packages "" ""
        if RUKEBOX_BOOT_TWEAKS="$(setting boot_tweaks yes)" \
           RUKEBOX_OVERCLOCK="no" \
           RUKEBOX_SYSTEM_UPGRADE="$(setting system_upgrade no)" \
           RUKEBOX_WIFI_COUNTRY="$COUNTRY" \
           RUKEBOX_AP_SSID="$AP_SSID" RUKEBOX_AP_PASSWORD="$AP_PASSWORD" \
           RUKEBOX_AP_SKIP=1 \
           RUKEBOX_WEB_PASSWORD="$(setting web_password)" \
           run "$PROJECT_ROOT/scripts/install.sh" </dev/null; then
            break
        fi
        status 4 install.step_packages "" install_failed
        [ "$attempt" -ge 5 ] && { echo "Giving up after $attempt attempts - see this log."; \
            cp "$LOG_FILE" "$BOOT_DIR/rukebox-install.log" 2>/dev/null; sleep infinity; }
        attempt=$((attempt + 1))
        sleep 60
    done
    done_step packages
fi

if nmcli -t -f NAME connection show 2>/dev/null | grep -qx "$HOME_CONN"; then
    run python3 /opt/rukebox/src/config_file.py set "HOME_WIFI_CONN_NAME=$HOME_CONN" >/dev/null || true
fi

if ! is_done files; then
    status 5 install.step_files
    if [ -d "$BOOT_DIR/rukebox-media" ]; then
        run mkdir -p "$AUDIO_DIR"
        run cp -r "$BOOT_DIR/rukebox-media/." "$AUDIO_DIR/" \
            && run chown -R pi:pi "$AUDIO_DIR" \
            && run rm -rf "$BOOT_DIR/rukebox-media"
    fi
    done_step files
fi

status 6 install.step_finish
run systemctl disable rukebox-firstboot.service
run rm -rf "$STATE_DIR"
cp "$LOG_FILE" "$BOOT_DIR/rukebox-install.log" 2>/dev/null || true
status 6 install.done
echo "$(date) - installation complete, rebooting into the radio"
sleep 8
stop_status_server
run systemctl reboot
