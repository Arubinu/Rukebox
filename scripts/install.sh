#!/bin/bash
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "Run this script with sudo." >&2
    exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---------------------------------------------------------------------------
# Which machine is this?
#
# A Raspberry Pi gets everything. An LXC container (Proxmox, or any host with
# lxc) keeps systemd, the units, the updater and the web interface, and loses
# what a container cannot have: the access point, the USB gadget, the hardware
# clock, GPIO, the activity LED. src/platform.py reaches the same conclusion
# at run time, from the same signs - this is the installer's own copy, for
# the things that are decided before anything runs.
#
# RUKEBOX_PROFILE=pi|lxc forces it, which is what the tests use.
# RUKEBOX_SKIP_INTERNET_CHECK=yes goes past the access probe (an apt proxy
# answers on neither port it tries).
# ---------------------------------------------------------------------------
# What systemd says this machine is running in. LXC creates no /dev/lxc and
# leaves `container=lxc` in the environment of PID 1 alone - neither reaches a
# shell - so that file is what tells a Proxmox container from a Pi here.
CONTAINER_KIND=""
if [ -r /run/systemd/container ]; then
    CONTAINER_KIND="$(tr -d '[:space:]' < /run/systemd/container)"
elif [ -r /proc/1/environ ]; then
    CONTAINER_KIND="$(tr '\0' '\n' < /proc/1/environ | sed -n 's/^container=//p' | head -n1)"
fi
if [ -n "${RUKEBOX_PROFILE:-}" ]; then
    PROFILE="$RUKEBOX_PROFILE"
elif [ -e /dev/lxc ] || [ "${container:-}" = "lxc" ] || [[ "$CONTAINER_KIND" == *lxc* ]]; then
    PROFILE="lxc"
else
    PROFILE="pi"
fi
if [ "$PROFILE" != "pi" ] && [ "$PROFILE" != "lxc" ]; then
    echo "RUKEBOX_PROFILE must be 'pi' or 'lxc' (got '$PROFILE')." >&2
    exit 2
fi

# The account the services run as. On a Pi that is the image's own user; in a
# container it is a system account created below, and root is not one of them.
if [ "$PROFILE" = "pi" ]; then
    RUN_USER="${RUKEBOX_USER:-pi}"
    AUDIO_ROOT="${RUKEBOX_AUDIO_ROOT:-/home/pi/audio}"
    RTC_REQUIRED=1
else
    RUN_USER="${RUKEBOX_USER:-rukebox}"
    AUDIO_ROOT="${RUKEBOX_AUDIO_ROOT:-/srv/rukebox/audio}"
    # A container shares the host's clock and has no I2C bus of its own.
    RTC_REQUIRED=0
fi

if [ "$PROFILE" = "lxc" ]; then
    echo "== Container installation (LXC profile) =="
    echo "This keeps the daemon, the web interface, the schedules, the"
    echo "statistics and the updater. It does NOT install the access point,"
    echo "the USB gadget, the hardware clock, GPIO or the activity LED: a"
    echo "container has none of them. Set RUKEBOX_PROFILE=pi to insist."
    echo ""
fi

if [ "$PROFILE" = "pi" ]; then
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
# Already applied, and the Wi-Fi password sits there in clear.
BOOT_DIR="/boot/firmware"
[ -d "$BOOT_DIR" ] || BOOT_DIR="/boot"
for leftover in user-data meta-data network-config; do
    [ -f "$BOOT_DIR/$leftover" ] && rm -f "$BOOT_DIR/$leftover"
done

else
    # The container profile: none of the above means anything in there, and
    # none of it can be undone with the SD card in another computer.
    echo "== Skipping the Pi's boot configuration =="
    echo "  boot target, cloud-init, config.txt, the RTC overlay, the Imager"
    echo "  files on the boot partition: none of these exist in a container."
fi

echo "== Checking Internet access (needed once, for apt/pip) =="
# Three tries: a container's network is often still coming up when the
# installer starts, and a lease that is a second late is not a missing host.
# Both ports, because apt fetches over 80 and a network that only filters 443
# would look dead here while apt works perfectly.
internet="no"
if [ "${RUKEBOX_SKIP_INTERNET_CHECK:-}" = "yes" ]; then
    internet="skipped"
else
    for attempt in 1 2 3; do
        for port in 80 443; do
            if timeout 5 bash -c "cat < /dev/null > /dev/tcp/deb.debian.org/$port" 2>/dev/null; then
                internet="yes"
                break 2
            fi
        done
        [ "$attempt" -lt 3 ] && sleep 3
    done
fi
if [ "$internet" = "no" ]; then
    echo "ERROR: no Internet access detected (could not reach deb.debian.org on" >&2
    echo "port 80 or 443)." >&2
    if [ "$PROFILE" = "lxc" ]; then
        echo "A container borrows its host's network: check that it has one" >&2
        echo "('ip -brief address', 'cat /etc/resolv.conf', 'apt-get update')." >&2
        echo "If apt goes through a proxy this probe cannot see it: run it again" >&2
        echo "with RUKEBOX_SKIP_INTERNET_CHECK=yes." >&2
    else
        echo "This Pi has no Wi-Fi by design - see README, 'Installing on a blank" >&2
        echo "SD card', step 4 for how to get TEMPORARY access just for this step" >&2
        echo "(Internet sharing over the USB cable, or a nearby Wi-Fi hotspot)." >&2
        echo "If apt goes through a proxy this probe cannot see it: run it again" >&2
        echo "with RUKEBOX_SKIP_INTERNET_CHECK=yes." >&2
    fi
    exit 1
fi

echo "== Installing system packages =="
apt-get update
# rtkit gives the audio server realtime priority, so a busy moment does not make the sound stutter.
# util-linux-extra carries hwclock, which writes a time set by hand into the clock module.
# A container template carries neither sudo nor curl: the first is what visudo
# and the interface's own systemctl calls need, the second what the Flic SDK
# helper downloads with.
apt-get install -y mpv python3 python3-pip python3-yaml bluez ffmpeg rtkit util-linux-extra \
    sudo curl \
    espeak-ng \
    pipewire pipewire-bin wireplumber pipewire-audio \
    pipewire-pulse pulseaudio-utils
# pipewire-pulse and pulseaudio-utils carry `pactl`, which the network stream
# uses to find the output to encode (and which the audio diagnostic reports
# with). `pactl` answers where `pw-dump` cannot - notably inside a container -
# so having both is what keeps the stream findable everywhere.
# pico2wave is the nicer French voice and not in every Debian (trixie dropped
# it): src/speech.py tries it first and then uses espeak-ng, so its absence is
# not a reason to stop the installation.
apt-get install -y libttspico-utils \
    || echo ">> pico2wave (libttspico-utils) is not in this release: announcements will use espeak-ng."
# WirePlumber's Bluetooth monitor waits for an "active" seat a headless Pi never has.
install -D -m 644 -o root -g root "$PROJECT_ROOT/config/wireplumber-bluez.conf" \
    /etc/wireplumber/wireplumber.conf.d/10-rukebox-bluez.conf
# A lingering "manager" session never gets rtkit priority: these limits let PipeWire take it itself.
install -D -m 644 -o root -g root "$PROJECT_ROOT/config/rt-limits.conf" \
    /etc/systemd/system/user@.service.d/10-rukebox-rt.conf
install -D -m 644 -o root -g root "$PROJECT_ROOT/config/journald-rukebox.conf" \
    /etc/systemd/journald.conf.d/50-rukebox.conf
install -D -m 644 -o root -g root "$PROJECT_ROOT/config/rtkit-quiet.conf" \
    /etc/systemd/system/rtkit-daemon.service.d/50-rukebox-quiet.conf
# A container has no journald of its own to reload (and `journalctl --flush`
# there waits for a bus that is not coming).
if [ "$PROFILE" = "pi" ]; then
    systemctl restart systemd-journald && journalctl --flush || true
fi
systemctl daemon-reload 2>/dev/null || true
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
mkdir -p "$AUDIO_ROOT"/{music,memes,morning_announcements,cutoff_announcements,doubleclick_announcements,system}

if [ "$PROFILE" = "lxc" ] && ! id -u "$RUN_USER" > /dev/null 2>&1; then
    echo "== Creating the '$RUN_USER' account the services run as =="
    useradd --system --create-home --home-dir "/home/$RUN_USER" --shell /usr/sbin/nologin "$RUN_USER"
fi

echo "== Copying files =="
cp -r "$PROJECT_ROOT/src" /opt/rukebox/
cp -r "$PROJECT_ROOT/scripts" /opt/rukebox/
cp -r "$PROJECT_ROOT/web" /opt/rukebox/
cp -r "$PROJECT_ROOT/config" /opt/rukebox/
chmod +x /opt/rukebox/scripts/*.sh /opt/rukebox/src/*.py

echo "== Copying confirmation sounds =="
cp "$PROJECT_ROOT"/assets/sounds/*.wav "$AUDIO_ROOT/system/"

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

if [ "$PROFILE" = "lxc" ]; then
    # The template documents the Pi's own folders; this machine's are elsewhere.
    python3 "$PROJECT_ROOT/src/config_file.py" set \
        "MUSIC_DIR=$AUDIO_ROOT/music" \
        "MEME_DIR=$AUDIO_ROOT/memes" \
        "CUTOFF_ANNOUNCE_DIR=$AUDIO_ROOT/cutoff_announcements" \
        "KEEPALIVE_SOUND=$AUDIO_ROOT/system/keepalive.wav" \
        "CLOCK_OK_SOUND=$AUDIO_ROOT/system/clock_ok.wav" \
        "CLOCK_FALLBACK_SOUND=$AUDIO_ROOT/system/clock_fallback.wav" \
        "RESTART_SOUND=$AUDIO_ROOT/system/restart.wav" \
        "AP_CONNECT_SOUND=" \
        "BATTERY_LOW_SOUND=" > /dev/null
    echo ">> Audio folders moved to $AUDIO_ROOT (the template documents the Pi's)."
fi

# WirePlumber reads the offered codecs from a drop-in generated from the config.
python3 "$PROJECT_ROOT/src/bt_codec.py" write >/dev/null 2>&1 \
    || echo "WARNING: could not write the Bluetooth codec drop-in." >&2

chown -R "$RUN_USER:$RUN_USER" /opt/rukebox /var/lib/rukebox "$AUDIO_ROOT" /etc/rukebox

echo "== Passwordless sudo for shutdown, clock, and web admin =="
SUDOERS_FILE=/etc/sudoers.d/rukebox-poweroff
SUDOERS_CANDIDATE="$(mktemp)"
# sudoers rejects the whole file over one CR (a checkout made on Windows).
# The grants name the account the services run as, which is not pi everywhere.
tr -d '\r' < "$PROJECT_ROOT/config/sudoers-rukebox" | sed "s/^pi /$RUN_USER /" > "$SUDOERS_CANDIDATE"
if ! command -v visudo > /dev/null 2>&1; then
    echo "!! visudo is not on this machine (the 'sudo' package), so the sudoers" >&2
    echo "!! rules were not installed. The web interface will not be able to" >&2
    echo "!! apply settings. Install sudo and run this script again." >&2
elif visudo -cqf "$SUDOERS_CANDIDATE"; then
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

echo "== Adding $RUN_USER to the bluetooth group (for bluetoothctl without sudo) =="
getent group bluetooth > /dev/null 2>&1 && usermod -aG bluetooth "$RUN_USER" || true

echo "== Recording the installed version =="
# The setup page stamps the release it was built from; a plain checkout has none.
RELEASE_TAG="$(cat "$PROJECT_ROOT/RELEASE" 2>/dev/null || true)"
python3 "$PROJECT_ROOT/src/version.py" write /var/lib/rukebox/version.json "$PROJECT_ROOT" install \
    ${RELEASE_TAG:+"$RELEASE_TAG"} > /dev/null
chown "$RUN_USER:$RUN_USER" /var/lib/rukebox/version.json

echo "== Installing systemd services =="
install -m 644 "$PROJECT_ROOT/systemd/"*.service /etc/systemd/system/
# The units are written for the Pi's own user; this machine may not have one.
if [ "$RUN_USER" != "pi" ]; then
    for unit in /etc/systemd/system/rukebox-*.service /etc/systemd/system/bt-connect.service \
                /etc/systemd/system/flic*.service /etc/systemd/system/home-wifi-connect.service \
                /etc/systemd/system/create-uap0.service; do
        [ -f "$unit" ] || continue
        sed -i "s|^User=pi$|User=$RUN_USER|" "$unit"
    done
    echo ">> Services run as $RUN_USER."
fi
# A container may have systemd running (Proxmox, and most LXC images) or not
# at all: enabling a unit is never a reason to stop the installation.
systemctl daemon-reload 2>/dev/null || true

# What a container has no use for, and what it keeps. Enabling a unit whose
# hardware is absent only gives a red line at every boot.
LXC_UNITS="create-uap0.service rukebox-bt-radio.service rukebox-usb-gadget.service
           rukebox-act-led.service rukebox-gpio-reset.service home-wifi-connect.service
           rukebox-card-reader.service flicd.service flic-bridge.service"
PI_UNITS="rukebox-gpio-reset.service bt-connect.service home-wifi-connect.service
          create-uap0.service rukebox-bt-radio.service rukebox-usb-gadget.service
          rukebox-act-led.service rukebox-card-reader.service rukebox-speaker-buttons.service"

systemctl enable rukebox-config.service 2>/dev/null || true
systemctl enable rukebox-daemon.service 2>/dev/null || true
systemctl enable rukebox-web.service 2>/dev/null || true
if [ "$PROFILE" = "lxc" ]; then
    for unit in $LXC_UNITS; do
        systemctl disable "$unit" 2>/dev/null || true
    done
    echo ">> Skipped the units a container cannot use (access point, USB gadget,"
    echo "   LED, GPIO, hardware clock, personal Wi-Fi, the Flic button)."
else
    for unit in $PI_UNITS; do
        systemctl enable "$unit" 2>/dev/null || true
    done
fi

if [ "$PROFILE" = "lxc" ]; then
    echo "== Not enabling lingering =="
    echo "   A container has no login session to keep alive; PipeWire is run by"
    echo "   the host, or by the stream alone."
else
    echo "== Keeping the audio stack alive without a login session =="
    loginctl enable-linger "$RUN_USER" || echo "WARNING: could not enable lingering for $RUN_USER" >&2
fi

if [ "$PROFILE" = "pi" ]; then
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

else
    # No radio to share: the container is reached on the host's own address.
    echo ""
    echo "== No access point, no Wi-Fi country =="
    echo "   A container has no radio of its own. Reach the interface on"
    echo "   http://<this host's address>:${WEB_PORT:-80} - see docs/guide.md,"
    echo "   'Running in a container'."
    AP_SSID=""
    # The web password below asks whether to reuse it; a container never set one.
    AP_PASSWORD=""
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
            read -rsp "Web interface password (Enter = none - 8 characters minimum otherwise): " WEB_PASSWORD
            echo ""
            { [ -z "$WEB_PASSWORD" ] || [ "${#WEB_PASSWORD}" -ge 8 ]; } && break
            echo "Must be at least 8 characters, or empty for none." >&2
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
unset AP_PASSWORD WEB_PASSWORD WEB_PASSWORD_HASH 2>/dev/null || true

if [ "$PROFILE" = "lxc" ]; then
echo ""
echo "== Done =="
echo ""
# shellcheck disable=SC1091
[ -f /etc/rukebox/rukebox.env ] && source /etc/rukebox/rukebox.env
echo "Reach the interface at http://<this host's address>:${WEB_PORT:-80}"
echo "There is no access point to join and no password by default: put one on"
echo "(Security card) if anything else can reach this address."
echo ""
echo "Next steps:"
echo "1. Edit /etc/rukebox/rukebox.yaml (times, folders) - or do it all from"
echo "   the web interface once you can reach it"
echo "2. Drop your audio files in $AUDIO_ROOT/ (subfolders accepted)"
echo "3. Sound: a container has no card of its own. Either give it one (a USB"
echo "   card passed through to it) or turn on the network stream and listen"
echo "   with 'Listen here', VLC or a network speaker:"
echo "     Settings > Audio > Network audio stream"
echo "4. Bluetooth works through the host's BlueZ, if the container was given"
echo "   the D-Bus socket. See docs/guide.md, 'Running in a container'."
echo "5. Start the radio and the web interface:"
echo "     systemctl start rukebox-daemon.service rukebox-web.service"
echo "6. Check the logs:"
echo "     journalctl -u rukebox-daemon.service -f"
echo ""
echo "To update later, from a Git repository (needs network):"
echo "     sudo rukebox-update --from-git https://github.com/you/your-repo.git"
echo "   Rollback:  sudo rukebox-update --rollback"
else
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
fi
