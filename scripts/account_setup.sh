#!/bin/bash
# Sets up the "pi" account: firstrun.sh calls it best effort, then
# rukebox-account.service calls it again once the image has created the account.
#
# ACCOUNT_MANAGE=yes sets the password (locked when only a key is given), =key
# adds the key and keeps Imager's password, =no leaves the account to Imager.
# Every step is best effort: it must never block the installation.

set -u

ENV_FILE="${RUKEBOX_ACCOUNT_ENV:-/etc/rukebox/account-setup.env}"
BOOT_DIR="${RUKEBOX_BOOT_DIR:-/boot/firmware}"
[ -d "$BOOT_DIR" ] || BOOT_DIR="/boot"
# The card is the fallback: firstrun.sh moves its copy to the rootfs.
CARD_ENV="$BOOT_DIR/rukebox-account.env"
[ -f "$ENV_FILE" ] || [ ! -f "$CARD_ENV" ] || ENV_FILE="$CARD_ENV"
[ -f "$ENV_FILE" ] && . "$ENV_FILE"

USERNAME="${ACCOUNT_USER:-pi}"
MANAGE="${ACCOUNT_MANAGE:-no}"
WAIT="${ACCOUNT_WAIT_SEC:-120}"
EXTRA_GROUPS="sudo,audio,bluetooth,netdev,dialout,plugdev,video,render"
# Not GROUPS: bash ignores assignments to its own GROUPS array.
# Fixed placeholder: userconf-pi wants a valid hash, the real password is set below.
PLACEHOLDER_HASH='$6$raspberryradiose$sIhB.tiKCeajySW5V7Cxf.HEcDgTxmNScaWIf4ZvLug1ICXRdWe7Gy47YOpnYJUmaIrDqg/BpmQzMFeI5QKmp1'

LOG="/var/log/rukebox-account.log"
if touch "$BOOT_DIR/.rukebox-log-test" 2>/dev/null; then
    rm -f "$BOOT_DIR/.rukebox-log-test"
    LOG="$BOOT_DIR/rukebox-account.log"
fi
exec >> "$LOG" 2>&1
echo ""
echo "$(date) - rukebox account setup (user=$USERNAME, manage=$MANAGE, wait=${WAIT}s)"

fail() { echo "ERROR: $*"; }

decode() { printf '%s' "${1:-}" | base64 -d 2>/dev/null || true; }
PASSWORD="$(decode "${ACCOUNT_PASSWORD_B64:-}")"
SSH_PUBKEY="$(decode "${ACCOUNT_SSH_KEY_B64:-}")"
echo "from $ENV_FILE: password=$([ -n "$PASSWORD" ] && echo yes || echo no) key=$([ -n "$SSH_PUBKEY" ] && echo yes || echo no)"

uid_of() { id -u "$1" 2>/dev/null; }

waited=0
while [ -z "$(uid_of "$USERNAME")" ] && [ "$waited" -lt "$WAIT" ]; do
    sleep 2
    waited=$((waited + 2))
done

if [ -z "$(uid_of "$USERNAME")" ] && [ "$MANAGE" != "no" ] && [ -x /usr/lib/userconf-pi/userconf ]; then
    echo "no $USERNAME account yet: userconf-pi renames the image's first user"
    /usr/lib/userconf-pi/userconf "$USERNAME" "$PLACEHOLDER_HASH" || fail "userconf-pi failed"
fi

if [ -z "$(uid_of "$USERNAME")" ] && [ "$MANAGE" != "no" ]; then
    # Same by hand, without userconf-pi - not useradd: the image's first user is the account.
    FIRST="$(getent passwd 1000 | cut -d: -f1)"
    if [ -n "$FIRST" ] && [ "$FIRST" != "$USERNAME" ]; then
        echo "renaming the image's first user $FIRST to $USERNAME"
        usermod -l "$USERNAME" "$FIRST" || fail "usermod -l failed"
        groupmod -n "$USERNAME" "$FIRST" 2>/dev/null || true
    fi
fi

if [ -z "$(uid_of "$USERNAME")" ]; then
    fail "no $USERNAME account: no password or key can be set for it"
    exit 0
fi

# It would hang the boot waiting for a console; it has nothing left to do.
systemctl disable --now userconfig.service >/dev/null 2>&1 || true

GROUP="$(id -gn "$USERNAME" 2>/dev/null)"
[ -n "$GROUP" ] || GROUP="$USERNAME"
HOME_DIR="$(getent passwd "$USERNAME" | cut -d: -f6)"
WANT_HOME="${ACCOUNT_HOME:-/home/$USERNAME}"
[ -n "$HOME_DIR" ] || HOME_DIR="$WANT_HOME"

# The units, the sudoers file and sshd all expect the account's home here.
if [ "$HOME_DIR" != "$WANT_HOME" ]; then
    echo "the account's home is $HOME_DIR: moving it to $WANT_HOME"
    [ -d "$HOME_DIR" ] && [ ! -e "$WANT_HOME" ] && mv "$HOME_DIR" "$WANT_HOME" 2>/dev/null
    usermod -d "$WANT_HOME" "$USERNAME" || fail "could not set the home directory"
    HOME_DIR="$WANT_HOME"
fi
mkdir -p "$HOME_DIR" 2>/dev/null
chown "$USERNAME:$GROUP" "$HOME_DIR" 2>/dev/null || true

SHELL_NOW="$(getent passwd "$USERNAME" | cut -d: -f7)"
case "$SHELL_NOW" in
    "" | */nologin | */false)
        echo "the account's shell is '$SHELL_NOW': setting /bin/bash"
        usermod -s /bin/bash "$USERNAME" || fail "could not set the login shell" ;;
esac
usermod -aG "$EXTRA_GROUPS" "$USERNAME" 2>/dev/null || true

if [ -n "$PASSWORD" ] && [ "$MANAGE" != "no" ]; then
    echo "$USERNAME:$PASSWORD" | chpasswd || fail "chpasswd failed"
elif [ "$MANAGE" = "yes" ]; then
    passwd -l "$USERNAME" >/dev/null 2>&1 || usermod -L "$USERNAME" >/dev/null 2>&1 || true
else
    # Imager's password stays, unless it is the placeholder userconf.txt would set.
    case "$(getent shadow "$USERNAME" 2>/dev/null | cut -d: -f2)" in
        "$PLACEHOLDER_HASH" | "!$PLACEHOLDER_HASH")
            echo "the placeholder hash from userconf.txt is set: locking the password"
            passwd -l "$USERNAME" >/dev/null 2>&1 || usermod -L "$USERNAME" >/dev/null 2>&1 || true ;;
    esac
fi

if [ -n "$SSH_PUBKEY" ]; then
    install -d -m 700 -o "$USERNAME" -g "$GROUP" "$HOME_DIR/.ssh" 2>/dev/null || mkdir -p "$HOME_DIR/.ssh"
    KEYFILE="$HOME_DIR/.ssh/authorized_keys"
    touch "$KEYFILE" 2>/dev/null
    if ! grep -qxF "$SSH_PUBKEY" "$KEYFILE" 2>/dev/null; then
        printf '%s\n' "$SSH_PUBKEY" >> "$KEYFILE" || fail "could not write $KEYFILE"
    fi
    chown -R "$USERNAME:$GROUP" "$HOME_DIR/.ssh" 2>/dev/null || true
    chmod 700 "$HOME_DIR/.ssh" 2>/dev/null || true
    chmod 600 "$KEYFILE" 2>/dev/null || true
fi

reachable() {
    [ -s "$HOME_DIR/.ssh/authorized_keys" ] && return 0
    passwd -S "$USERNAME" 2>/dev/null | grep -q "^$USERNAME P " && return 0
    return 1
}

# Nobody could log in; the card's log is the only place that can say so.
if ! reachable; then
    echo "WARNING: no SSH key and no password: nobody can log in as $USERNAME."
fi

# rukebox-firstboot.service starts from this copy, which the hook boot cannot make.
if [ -d "$BOOT_DIR/rukebox" ]; then
    rm -rf "$HOME_DIR/rukebox"
    cp -r "$BOOT_DIR/rukebox" "$HOME_DIR/rukebox" || fail "could not copy the project"
    chown -R "$USERNAME:$GROUP" "$HOME_DIR/rukebox" 2>/dev/null || true
    chmod +x "$HOME_DIR/rukebox/scripts/"*.sh 2>/dev/null || true
fi

BUNDLE="$BOOT_DIR/rukebox-config.json"
if [ -f "$BUNDLE" ] && [ -f "$HOME_DIR/rukebox/src/config_bundle.py" ]; then
    echo "applying the configuration bundle $BUNDLE"
    python3 "$HOME_DIR/rukebox/src/config_bundle.py" import "$BUNDLE" || fail "the configuration bundle was refused"
fi

ok=1
[ -n "$(uid_of "$USERNAME")" ] || ok=0
case "$(getent passwd "$USERNAME" | cut -d: -f7)" in
    "" | */nologin | */false) ok=0 ;;
esac
if [ "$MANAGE" = "yes" ]; then
    if [ -n "$PASSWORD" ]; then
        passwd -S "$USERNAME" 2>/dev/null | grep -q "^$USERNAME P " || ok=0
    else
        passwd -S "$USERNAME" 2>/dev/null | grep -q "^$USERNAME L " || ok=0
    fi
fi
if [ -n "$SSH_PUBKEY" ]; then
    grep -qxF "$SSH_PUBKEY" "$HOME_DIR/.ssh/authorized_keys" 2>/dev/null || ok=0
fi
reachable || ok=0
[ -d "$BOOT_DIR/rukebox" ] && { [ -d "$HOME_DIR/rukebox/scripts" ] || ok=0; }

if [ "$ok" != "1" ]; then
    echo "not finished: rukebox-account.service tries again at the next boot"
    exit 0
fi
if [ "${ACCOUNT_FIRST_BOOT:-0}" = "1" ]; then
    # The image's account services have not run yet: the unit redoes this after them.
    echo "done for this boot: rukebox-account.service applies it again after cloud-init"
    exit 0
fi

echo "done: the $USERNAME account is usable"
# Left in place, userconf.txt would set its placeholder hash as the password later.
rm -f "$BOOT_DIR/userconf.txt" "$BOOT_DIR/userconf" "$CARD_ENV"
rm -f "$ENV_FILE"
systemctl disable rukebox-account.service >/dev/null 2>&1 || true
exit 0
