#!/bin/bash
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "Run this script with sudo." >&2
    exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== Boot tweaks (optional) =="
echo "These change the OS (not Rukebox), and are all reversible:"
echo "  - boot to console instead of the desktop  (headless device)"
echo "  - don't wait for network connectivity at boot"
echo "  - disable cloud-init                      (VM provisioning, unused here)"
echo "  - minimum GPU memory, no boot splash      (no screen attached)"
if [ -n "${RUKEBOX_BOOT_TWEAKS:-}" ]; then
    BOOT_TWEAKS="$RUKEBOX_BOOT_TWEAKS"
elif [ -t 0 ]; then
    read -rp "Apply them? (Y/n) " answer
    if [[ "$answer" =~ ^[nN] ]]; then BOOT_TWEAKS="no"; else BOOT_TWEAKS="yes"; fi
else
    BOOT_TWEAKS="no"
    echo "Non-interactive install: skipped (set RUKEBOX_BOOT_TWEAKS=yes to apply)."
fi

if [ "$BOOT_TWEAKS" = "yes" ]; then
    systemctl set-default multi-user.target > /dev/null

    systemctl disable NetworkManager-wait-online.service 2>/dev/null || true

    if [ -d /etc/cloud ]; then
        touch /etc/cloud/cloud-init.disabled
    fi

    CONFIG_TXT=""
    for candidate in /boot/firmware/config.txt /boot/config.txt; do
        [ -f "$candidate" ] && CONFIG_TXT="$candidate" && break
    done
    if [ -n "$CONFIG_TXT" ]; then
        grep -q "^disable_splash=" "$CONFIG_TXT" || echo "disable_splash=1" >> "$CONFIG_TXT"
        grep -q "^gpu_mem=" "$CONFIG_TXT" || echo "gpu_mem=16" >> "$CONFIG_TXT"
    else
        echo "config.txt not found - skipped that part." >&2
    fi
    echo "Applied (effective at the next boot)."

    if [ -n "${RUKEBOX_OVERCLOCK:-}" ]; then
        OVERCLOCK="$RUKEBOX_OVERCLOCK"
    elif [ -t 0 ]; then
        echo ""
        echo "Optional: mild overclock (1000 -> 1200 MHz, over_voltage=2)."
        echo "Expect only ~1-2s off boot (it is mostly I/O-bound), and it"
        echo "depends on your individual board - an unstable setting can stop"
        echo "the Pi booting, recoverable only via the SD card on a PC."
        read -rp "Enable it? (y/N) " answer
        if [[ "$answer" =~ ^[yY] ]]; then OVERCLOCK="yes"; else OVERCLOCK="no"; fi
    else
        OVERCLOCK="no"
    fi
    if [ "$OVERCLOCK" = "yes" ] && [ -n "$CONFIG_TXT" ]; then
        grep -q "^arm_freq=" "$CONFIG_TXT" || echo "arm_freq=1200" >> "$CONFIG_TXT"
        grep -q "^over_voltage=" "$CONFIG_TXT" || echo "over_voltage=2" >> "$CONFIG_TXT"
        echo "Overclock written. If the Pi ever fails to boot, delete those two"
        echo "lines from config.txt with the SD card in another computer."
    fi
else
    echo "Skipped. To apply them later, by hand:"
    echo "  sudo systemctl set-default multi-user.target"
    echo "  sudo systemctl disable NetworkManager-wait-online.service"
    echo "  sudo touch /etc/cloud/cloud-init.disabled"
    echo "  echo -e 'disable_splash=1\\ngpu_mem=16' | sudo tee -a /boot/firmware/config.txt"
fi

CONFIG_TXT=""
for candidate in /boot/firmware/config.txt /boot/config.txt; do
    [ -f "$candidate" ] && CONFIG_TXT="$candidate" && break
done
if [ -n "$CONFIG_TXT" ]; then
    grep -qE "^[[:space:]]*dtparam=i2c(_arm|1)?=on" "$CONFIG_TXT" || echo "dtparam=i2c_arm=on" >> "$CONFIG_TXT"
    grep -qE "^[[:space:]]*dtoverlay=i2c-rtc" "$CONFIG_TXT" || echo "dtoverlay=i2c-rtc,ds3231" >> "$CONFIG_TXT"
    echo "I2C and the DS3231 RTC overlay are configured (effective at the next boot)."
    echo "  To undo: remove the dtparam=i2c_arm / dtoverlay=i2c-rtc lines from $CONFIG_TXT"
else
    echo "config.txt not found - add these two lines by hand for the RTC:" >&2
    echo "  dtparam=i2c_arm=on" >&2
    echo "  dtoverlay=i2c-rtc,ds3231" >&2
fi

echo "== Taking Imager's settings off the boot partition =="
# Already applied, and readable from any computer: the Wi-Fi password is in clear.
BOOT_DIR="/boot/firmware"
[ -d "$BOOT_DIR" ] || BOOT_DIR="/boot"
for leftover in user-data meta-data network-config; do
    [ -f "$BOOT_DIR/$leftover" ] && rm -f "$BOOT_DIR/$leftover"
done

echo "== Checking Internet access (needed once, for apt/pip) =="
if ! timeout 5 bash -c 'cat < /dev/null > /dev/tcp/deb.debian.org/443' 2>/dev/null; then
    echo "ERROR: no Internet access detected (could not reach deb.debian.org:443)." >&2
    echo "This Pi has no Wi-Fi by design - see README, 'Installing on a blank" >&2
    echo "SD card', step 4 for how to get TEMPORARY access just for this step" >&2
    echo "(Internet sharing over the USB cable, or a nearby Wi-Fi hotspot)." >&2
    exit 1
fi

echo "== Installing system packages =="
apt-get update
# rtkit: realtime priority for PipeWire, so a busy moment does not make the
# Bluetooth sound stutter.
apt-get install -y mpv python3 python3-pip python3-yaml bluez ffmpeg rtkit
pip3 install bleak flask --break-system-packages

if [ -n "${RUKEBOX_SYSTEM_UPGRADE:-}" ]; then
    SYSTEM_UPGRADE="$RUKEBOX_SYSTEM_UPGRADE"
elif [ -t 0 ]; then
    echo ""
    echo "Optional: also upgrade the installed system packages now"
    echo "(apt-get upgrade - several minutes on a Pi Zero 2 W)."
    read -rp "Do it? (y/N) " answer
    if [[ "$answer" =~ ^[yY] ]]; then SYSTEM_UPGRADE="yes"; else SYSTEM_UPGRADE="no"; fi
else
    SYSTEM_UPGRADE="no"
fi
if [ "$SYSTEM_UPGRADE" = "yes" ]; then
    echo "== Upgrading the system packages (this takes a few minutes) =="
    apt-get -y upgrade
else
    echo "System packages not upgraded (do it later with: sudo apt update && sudo apt full-upgrade)."
fi

echo "== Creating folders =="
mkdir -p /opt/rukebox /etc/rukebox /var/lib/rukebox
mkdir -p /home/pi/audio/{music,memes,morning_announcements,cutoff_announcements,doubleclick_announcements,system}

echo "== Copying files =="
cp -r "$PROJECT_ROOT/src" /opt/rukebox/
cp -r "$PROJECT_ROOT/scripts" /opt/rukebox/
cp -r "$PROJECT_ROOT/web" /opt/rukebox/
cp -r "$PROJECT_ROOT/config" /opt/rukebox/
chmod +x /opt/rukebox/scripts/*.sh /opt/rukebox/src/*.py

echo "== Copying confirmation sounds =="
cp "$PROJECT_ROOT"/assets/sounds/*.wav /home/pi/audio/system/

action=$(python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT/src')
import config_file
what, count = config_file.ensure_file()
print(what, count)
")
case "$action" in
    "created "*)   echo ">> /etc/rukebox/rukebox.yaml created: REMEMBER TO EDIT IT (speaker MAC, folders, times)." ;;
    *)             echo ">> /etc/rukebox/rukebox.yaml already present, left untouched." ;;
esac

chown -R pi:pi /opt/rukebox /var/lib/rukebox /home/pi/audio /etc/rukebox

echo "== Passwordless sudo for shutdown, clock, and web admin =="
SUDOERS_FILE=/etc/sudoers.d/rukebox-poweroff
SUDOERS_CANDIDATE="$(mktemp)"
# sudoers rejects the whole file over one CR (a checkout made on Windows).
tr -d '\r' < "$PROJECT_ROOT/config/sudoers-rukebox" > "$SUDOERS_CANDIDATE"
if visudo -cqf "$SUDOERS_CANDIDATE"; then
    install -m 440 -o root -g root "$SUDOERS_CANDIDATE" "$SUDOERS_FILE"
else
    echo "!! config/sudoers-rukebox rejected by visudo, not installed." >&2
    echo "!! The web interface will not be able to apply settings." >&2
fi
rm -f "$SUDOERS_CANDIDATE"

echo "== Installing the updater =="
install -m 755 -o root -g root "$PROJECT_ROOT/scripts/update.sh" /usr/local/sbin/rukebox-update

echo "== Installing the USB network gadget helper =="
install -m 755 -o root -g root "$PROJECT_ROOT/scripts/usb_gadget.sh" /usr/local/sbin/rukebox-usb-gadget

echo "== Installing the account helper =="
install -m 755 -o root -g root "$PROJECT_ROOT/scripts/account_setup.sh" /usr/local/sbin/rukebox-account-setup

echo "== Installing the Flic SDK helper =="
install -m 755 -o root -g root "$PROJECT_ROOT/scripts/install_flic_sdk.sh" /usr/local/sbin/rukebox-flic-sdk
if [ ! -d /opt/fliclib-linux-hci ]; then
    /usr/local/sbin/rukebox-flic-sdk || echo "   Flic SDK not downloaded (no Internet?) - possible later from the web interface."
fi

echo "== Adding user pi to the bluetooth group (for bluetoothctl without sudo) =="
usermod -aG bluetooth pi || true

echo "== Recording the installed version =="
# The setup page stamps the release it was built from; a plain checkout has none.
RELEASE_TAG="$(cat "$PROJECT_ROOT/RELEASE" 2>/dev/null || true)"
python3 "$PROJECT_ROOT/src/version.py" write /var/lib/rukebox/version.json "$PROJECT_ROOT" install \
    ${RELEASE_TAG:+"$RELEASE_TAG"} > /dev/null
chown pi:pi /var/lib/rukebox/version.json

echo "== Installing systemd services =="
cp "$PROJECT_ROOT/systemd/"*.service /etc/systemd/system/
systemctl daemon-reload

systemctl enable rukebox-config.service
systemctl enable rukebox-gpio-reset.service
systemctl enable bt-connect.service
systemctl enable rukebox-daemon.service
systemctl enable rukebox-web.service
systemctl enable create-uap0.service
systemctl enable rukebox-bt-radio.service
systemctl enable rukebox-usb-gadget.service
systemctl enable rukebox-act-led.service
systemctl enable rukebox-speaker-buttons.service

echo "== Keeping the audio stack alive without a login session =="
loginctl enable-linger pi || echo "WARNING: could not enable lingering for pi" >&2

echo ""
echo "== Wi-Fi regulatory domain (country) =="
if [ -n "${RUKEBOX_WIFI_COUNTRY:-}" ]; then
    WIFI_COUNTRY="$RUKEBOX_WIFI_COUNTRY"
elif [ -t 0 ]; then
    read -rp "Wi-Fi country code (2 letters, e.g. FR, US, DE - blank to skip): " WIFI_COUNTRY
else
    WIFI_COUNTRY=""
fi
if [ -n "$WIFI_COUNTRY" ] && [[ ! "$WIFI_COUNTRY" =~ ^[A-Za-z]{2}$ ]]; then
    echo "'$WIFI_COUNTRY' doesn't look like a 2-letter country code - skipping." >&2
    WIFI_COUNTRY=""
fi
if [ -n "$WIFI_COUNTRY" ]; then
    WIFI_COUNTRY="${WIFI_COUNTRY^^}"
    if command -v raspi-config > /dev/null 2>&1; then
        raspi-config nonint do_wifi_country "$WIFI_COUNTRY"
        echo "Wi-Fi country set to $WIFI_COUNTRY."
    else
        echo "raspi-config not found (not a Raspberry Pi OS image?) - skipped." >&2
        echo "The access point below may fail to start without this." >&2
    fi
else
    echo "No Wi-Fi country set - the access point still works, but the radio" >&2
    echo "may not be using the channels/power allowed where you are. Set it" >&2
    echo "any time with:  sudo raspi-config nonint do_wifi_country XX" >&2
fi

echo ""
echo "== Admin Wi-Fi access point =="
systemctl enable --now create-uap0.service

echo ""
echo "== Bluetooth radio =="
systemctl enable --now rukebox-bt-radio.service

if [ -n "${RUKEBOX_AP_SSID:-}" ]; then
    AP_SSID="$RUKEBOX_AP_SSID"
    AP_PASSWORD="${RUKEBOX_AP_PASSWORD:-}"
elif [ -t 0 ]; then
    read -rp "Access point name (SSID) [Rukebox-Admin]: " AP_SSID
    AP_SSID="${AP_SSID:-Rukebox-Admin}"
    while true; do
        read -rsp "Access point password (Enter = open network, no password - 8 characters minimum otherwise): " AP_PASSWORD
        echo ""
        { [ -z "$AP_PASSWORD" ] || [ "${#AP_PASSWORD}" -ge 8 ]; } && break
        echo "Must be at least 8 characters, or empty for an open network." >&2
    done
    if [ -z "$AP_PASSWORD" ]; then
        echo "WARNING: the access point will be OPEN - anyone in range can connect and reach the admin interface."
    fi
else
    AP_SSID="Rukebox-Admin"
    AP_PASSWORD=""
    echo "Non-interactive install: access point left OPEN (SSID '$AP_SSID', no password)."
    echo "Change this any time from the web interface's Access Point card, or:"
    echo "  sudo /opt/rukebox/scripts/setup_ap.sh <ssid> <password>"
fi
if [ "${RUKEBOX_AP_SKIP:-0}" = "1" ]; then
    echo "Access point already set up by the first-boot installation - left as is."
else
    "$PROJECT_ROOT/scripts/setup_ap.sh" "$AP_SSID" "$AP_PASSWORD"
fi

echo ""
echo "== Web interface password (optional, on top of the access point) =="
WEB_PASSWORD=""
if [ -n "${RUKEBOX_WEB_PASSWORD+x}" ]; then
    WEB_PASSWORD="$RUKEBOX_WEB_PASSWORD"
elif [ -t 0 ]; then
    reused=0
    if [ -n "$AP_PASSWORD" ]; then
        read -rp "Use the same password for the web interface too? (Y/n) " same
        if [[ ! "$same" =~ ^[nN] ]]; then
            WEB_PASSWORD="$AP_PASSWORD"
            reused=1
        fi
    fi
    if [ "$reused" != "1" ]; then
        while true; do
            read -rsp "Web interface password (Enter = none - 4 characters minimum otherwise): " WEB_PASSWORD
            echo ""
            { [ -z "$WEB_PASSWORD" ] || [ "${#WEB_PASSWORD}" -ge 4 ]; } && break
            echo "Must be at least 4 characters, or empty for none." >&2
        done
    fi
fi
if [ -n "$WEB_PASSWORD" ]; then
    WEB_PASSWORD_HASH=$(python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT/src')
import web_auth
print(web_auth.hash_password(sys.argv[1]))
" "$WEB_PASSWORD")
    python3 "$PROJECT_ROOT/src/config_file.py" set "WEB_PASSWORD_HASH=$WEB_PASSWORD_HASH" > /dev/null
    echo "Web interface password set."
else
    echo "No web interface password (the access point remains the only gate)."
fi
unset AP_PASSWORD WEB_PASSWORD WEB_PASSWORD_HASH

echo ""
echo "== Done =="
echo "!! IMPORTANT: this Pi has no Wi-Fi -> a hardware RTC module (DS3231)"
echo "!! is REQUIRED for a reliable clock. See docs/guide.md, Clock section,"
echo "!! before relying on scheduled events (announcement/cutoff)."
echo ""
# shellcheck disable=SC1091
[ -f /etc/rukebox/rukebox.env ] && source /etc/rukebox/rukebox.env
echo "Access point '$AP_SSID' is active on uap0 - connect your phone to it,"
echo "then reach the admin interface (both SSID and password, if any, are"
echo "editable later from its own card in the web interface):"
if [ "${WEB_PORT:-80}" = "80" ]; then
    echo "     http://<Pi's IP on that network>    (or just accept the"
    echo "     \"sign in to the network\" prompt your phone shows on connecting)"
else
    echo "     http://<Pi's IP on that network>:${WEB_PORT}"
fi
echo ""
echo "Next steps:"
echo "1. Edit /etc/rukebox/rukebox.yaml (speaker MAC, times, folders) -"
echo "   or do it all from the web interface once you can reach it"
echo "2. Drop your audio files in /home/pi/audio/ (subfolders accepted)"
echo "3. (Optional) Also connect the Pi to your personal network, in"
echo "   addition to the access point, for SSH access over that network:"
echo "     sudo /opt/rukebox/scripts/setup_home_wifi.sh"
echo "4. (Optional) Set up the Bluetooth clock fallback, with an"
echo "   immediate compatibility test of the device:"
echo "     sudo /opt/rukebox/scripts/setup_bt_clock.sh"
echo "5. Install and configure the Flic SDK (see docs/guide.md) then:"
echo "     systemctl enable --now flicd.service flic-bridge.service"
echo "6. (Optional) Instead of, or in addition to, the Flic button: wire a"
echo "   physical button to GPIO_BUTTON_PIN (default BCM 20) and GND, then:"
echo "     sudo systemctl enable --now rukebox-gpio-button.service"
echo "7. Start the radio and the web interface:"
echo "     systemctl start bt-connect.service rukebox-daemon.service rukebox-web.service"
echo "8. Check the logs:"
echo "     journalctl -u rukebox-daemon.service -f"
echo "9. Usage statistics are recorded automatically in"
echo "    /var/lib/rukebox/stats.db and shown in the web interface"
echo "    (Statistics card), where they can also be exported or reset."
echo ""
echo "To update later, from the computer holding this project:"
echo "     ./bootstrap/push_update.sh          (macOS/Linux)"
echo "     bootstrap\\push_update.cmd           (Windows)"
echo "   Or on the Pi itself, from a Git repository (needs network):"
echo "     sudo rukebox-update --from-git https://github.com/you/your-repo.git"
echo "   Rollback:  sudo rukebox-update --rollback"
