#!/bin/bash
set -euo pipefail

echo "=== Rukebox - provisioning a blank SD card ==="
echo ""

detect_boot_candidates() {
    local candidates=()
    local roots=()
    case "$(uname -s)" in
        Darwin) roots=(/Volumes/*) ;;
        Linux)  roots=(/media/"$USER"/* /run/media/"$USER"/* /mnt/*) ;;
    esac
    for dir in "${roots[@]}"; do
        [ -f "$dir/config.txt" ] && candidates+=("$dir")
    done
    printf '%s\n' "${candidates[@]}"
}

BOOT_PATH=""
mapfile -t DETECTED < <(detect_boot_candidates 2>/dev/null || true)

if [ "${#DETECTED[@]}" -eq 1 ]; then
    echo "Detected boot partition: ${DETECTED[0]}"
    read -rp "Use it? (Y/n) " confirm
    if [[ ! "$confirm" =~ ^[nN] ]]; then
        BOOT_PATH="${DETECTED[0]}"
    fi
elif [ "${#DETECTED[@]}" -gt 1 ]; then
    echo "Several candidate boot partitions found:"
    for i in "${!DETECTED[@]}"; do
        echo "  $((i+1))) ${DETECTED[$i]}"
    done
    read -rp "Number to use, or Enter to type a path manually: " choice
    if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#DETECTED[@]}" ]; then
        BOOT_PATH="${DETECTED[$((choice-1))]}"
    fi
fi

if [ -z "$BOOT_PATH" ]; then
    read -rp "Path to the boot partition (e.g. /Volumes/bootfs on Mac, /media/\$USER/bootfs on Linux): " BOOT_PATH
fi

if [ ! -d "$BOOT_PATH" ]; then
    echo "Folder not found: $BOOT_PATH" >&2
    exit 1
fi
if [ ! -f "$BOOT_PATH/config.txt" ]; then
    echo "config.txt missing from $BOOT_PATH: this doesn't look like the" >&2
    echo "boot partition of a Raspberry Pi OS image." >&2
    exit 1
fi

read -rsp "Password for the 'pi' user (leave empty for SSH key access only): " PI_PASSWORD
echo ""
read -rsp "Confirm the password (also empty if you left it empty): " PI_PASSWORD_CONFIRM
echo ""
if [ "$PI_PASSWORD" != "$PI_PASSWORD_CONFIRM" ]; then
    echo "Passwords don't match." >&2
    exit 1
fi

DEFAULT_KEY="$HOME/.ssh/id_ed25519.pub"
[ -f "$DEFAULT_KEY" ] || DEFAULT_KEY="$HOME/.ssh/id_rsa.pub"
read -rp "Path to your SSH public key (empty to skip) [$DEFAULT_KEY]: " SSH_KEY_PATH
SSH_KEY_PATH="${SSH_KEY_PATH:-$DEFAULT_KEY}"
SSH_PUBKEY=""
if [ -f "$SSH_KEY_PATH" ]; then
    SSH_PUBKEY=$(cat "$SSH_KEY_PATH")
    echo "SSH key found: $SSH_KEY_PATH"
else
    echo "No key found."
fi

if [ -z "$PI_PASSWORD" ] && [ -z "$SSH_PUBKEY" ]; then
    echo "Neither a password nor an SSH key: there would be no way to log into the Pi. Provide at least one of the two." >&2
    exit 1
fi
if [ -z "$PI_PASSWORD" ]; then
    echo "No password: the account will be locked, SSH key login only."
fi

read -rp "Pi hostname [rukebox]: " HOSTNAME_INPUT
HOSTNAME_INPUT="${HOSTNAME_INPUT:-rukebox}"

echo ""
echo "== Writing configuration to the card =="

if [ -n "$PI_PASSWORD" ] && ! command -v openssl >/dev/null 2>&1; then
    echo "openssl not found: required to hash the password for userconf.txt." >&2
    echo "Install it, or leave the password empty and use an SSH key instead." >&2
    exit 1
fi

touch "$BOOT_PATH/ssh"

FIXED_PLACEHOLDER_HASH='$6$raspberryradiose$sIhB.tiKCeajySW5V7Cxf.HEcDgTxmNScaWIf4ZvLug1ICXRdWe7Gy47YOpnYJUmaIrDqg/BpmQzMFeI5QKmp1'
if [ -n "$PI_PASSWORD" ]; then
    PW_HASH=$(printf '%s' "$PI_PASSWORD" | openssl passwd -6 -stdin)
else
    PW_HASH="$FIXED_PLACEHOLDER_HASH"
fi
printf 'pi:%s\n' "$PW_HASH" > "$BOOT_PATH/userconf.txt"

if ! grep -q "^# rukebox: USB gadget mode" "$BOOT_PATH/config.txt" 2>/dev/null; then
    cat >> "$BOOT_PATH/config.txt" <<'EOF'

[all]
# rukebox: USB gadget mode - makes the Pi appear as a USB network
# interface, so SSH works over the USB cable alone.
dtoverlay=dwc2,dr_mode=otg
EOF
fi

if ! grep -q "^# rukebox: I2C" "$BOOT_PATH/config.txt" 2>/dev/null; then
    cat >> "$BOOT_PATH/config.txt" <<'EOF'

[all]
# rukebox: I2C + DS3231 real-time clock.
dtparam=i2c_arm=on
dtoverlay=i2c-rtc,ds3231
EOF
fi

CMDLINE_FILE="$BOOT_PATH/cmdline.txt"
cp "$CMDLINE_FILE" "$CMDLINE_FILE.orig"
CMDLINE_CONTENT=$(tr -d '\n' < "$CMDLINE_FILE")
if [[ "$CMDLINE_CONTENT" != *"modules-load=dwc2"* ]]; then
    CMDLINE_CONTENT="${CMDLINE_CONTENT/rootwait/rootwait modules-load=dwc2}"
fi
if [ -f "$BOOT_PATH/firstrun.sh" ] && ! grep -q "rukebox" "$BOOT_PATH/firstrun.sh"; then
    mv "$BOOT_PATH/firstrun.sh" "$BOOT_PATH/rukebox-imager-firstrun.sh"
fi
CMDLINE_CONTENT="$(printf '%s' "$CMDLINE_CONTENT" | sed -E 's/ ?systemd\.run=[^ ]*//; s/ ?systemd\.run_success_action=[^ ]*//; s/ ?systemd\.unit=[^ ]*//')"
CMDLINE_CONTENT="$CMDLINE_CONTENT systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target"
printf '%s' "$CMDLINE_CONTENT" > "$CMDLINE_FILE"

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
rm -rf "$BOOT_PATH/rukebox"
cp -r "$PROJECT_ROOT" "$BOOT_PATH/rukebox"
rm -rf "$BOOT_PATH/rukebox/.git" 2>/dev/null || true

TEMPLATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PW_B64=$(printf '%s' "$PI_PASSWORD" | base64 | tr -d '\n')
SSHKEY_B64=$(printf '%s' "$SSH_PUBKEY" | base64 | tr -d '\n')

TEMPLATE_CONTENT="$(cat "$TEMPLATE_DIR/firstrun.sh.template")"
TEMPLATE_CONTENT="${TEMPLATE_CONTENT//__HOSTNAME__/$HOSTNAME_INPUT}"
printf '%s' "$TEMPLATE_CONTENT" > "$BOOT_PATH/firstrun.sh"
chmod +x "$BOOT_PATH/firstrun.sh" 2>/dev/null || true

cat > "$BOOT_PATH/rukebox-account.env" <<EOF
ACCOUNT_USER=pi
ACCOUNT_MANAGE=yes
ACCOUNT_PASSWORD_B64='$PW_B64'
ACCOUNT_SSH_KEY_B64='$SSHKEY_B64'
EOF
chmod 600 "$BOOT_PATH/rukebox-account.env" 2>/dev/null || true

echo ""
echo "== Done =="
echo "Safely eject the SD card, insert it into the Pi and power it on."
echo ""
echo "The Pi boots, runs the provisioning script automatically, then"
echo "REBOOTS ITSELF once done - you don't need to do anything, just"
echo "wait about 1-2 minutes."
echo ""
echo "Then:"
echo "1. Connect the Pi to this computer with a USB cable (the 'DATA'"
echo "   USB port, not 'PWR', on a Pi Zero/Zero 2 W)."
echo "2. ssh pi@169.254.7.7"
echo "   (fixed link-local address, works out of the box on macOS/Linux/"
echo "   Windows - no mDNS/Bonjour needed. Can also try"
echo "   ssh ${USER:-pi}@${HOSTNAME_INPUT}.local as a fallback where mDNS"
echo "   is available.)"
echo ""
echo "IMPORTANT: the radio software isn't installed yet at this stage"
echo "(that needs Internet access). See the README, 'Installing on a"
echo "blank SD card' section, for what's next."
