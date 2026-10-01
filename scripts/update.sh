#!/bin/bash
set -euo pipefail

# Runs from a copy: this file is replaced during the update and bash reads scripts lazily.
if [ "${RUKEBOX_UPDATE_REEXEC:-}" != "1" ]; then
    _self="$(readlink -f "${BASH_SOURCE[0]}")"
    _copy="$(mktemp /tmp/rukebox-update-XXXXXX.sh)"
    cp "$_self" "$_copy"
    chmod +x "$_copy"
    RUKEBOX_UPDATE_REEXEC=1 RUKEBOX_UPDATE_ORIGIN="$_self" exec "$_copy" "$@"
fi
trap 'rm -f "$0"' EXIT

INSTALL_DIR="${RUKEBOX_INSTALL_DIR:-/opt/rukebox}"
CONFIG_FILE="${RUKEBOX_CONFIG_FILE:-/etc/rukebox/rukebox.yaml}"
ENV_FILE="${RUKEBOX_ENV_FILE:-/etc/rukebox/rukebox.env}"
STATE_DIR="${RUKEBOX_STATE_DIR:-/var/lib/rukebox}"
AUDIO_SYSTEM_DIR="${RUKEBOX_AUDIO_SYSTEM_DIR:-/home/pi/audio/system}"
UPDATE_WRAPPER="${RUKEBOX_UPDATE_WRAPPER:-/usr/local/sbin/rukebox-update}"
SUDOERS_FILE="${RUKEBOX_SUDOERS_FILE:-/etc/sudoers.d/rukebox-poweroff}"
SYSTEMD_DIR="${RUKEBOX_SYSTEMD_DIR:-/etc/systemd/system}"
RUKEBOX_USER="${RUKEBOX_USER:-pi}"
BACKUP_DIR="$STATE_DIR/backups"
VERSION_FILE="$STATE_DIR/version.json"
GIT_CHECKOUT="$STATE_DIR/git-source"

SOURCE_DIR=""
RELEASE_TAG=""
RELEASE_STAMP=""
SOURCE_KIND=""
ARCHIVE=""
GIT_URL=""
GIT_BRANCH=""
BACKUP_KEEP=3
BACKUP_KEEP_SET=0
DO_RESTART=1
QUIET=0

say()  { [ "$QUIET" = "1" ] || echo "$@"; }
step() { [ "$QUIET" = "1" ] || echo "== $* =="; }
fail() { echo "ERROR: $*" >&2; exit 1; }

# The web page reads this to say "update in progress" instead of looking like a dead Pi.
# It carries a timestamp: the web server ignores one older than fifteen minutes.
UPDATING_FLAG="$STATE_DIR/updating"
mark_updating() { mkdir -p "$STATE_DIR"; date +%s > "$UPDATING_FLAG" 2>/dev/null || true; }
clear_updating() { rm -f "$UPDATING_FLAG" 2>/dev/null || true; }
trap 'rm -f "$0"; clear_updating' EXIT

usage() {
    cat <<'USAGE'
Usage: sudo update.sh [SOURCE] [OPTIONS]

Source (exactly one):
  --source DIR         Use an already-extracted project tree
  --from-archive FILE  Extract a .tar.gz, then use it
  --from-git [URL]     Clone/pull a Git repository (URL defaults to
                       UPDATE_GIT_URL in rukebox.yaml). Needs network.
  --from-release [TAG] Download a GitHub release (the latest one, or TAG)
                       of UPDATE_GITHUB_REPO in rukebox.yaml. Needs network.

Options:
  --branch NAME        Git branch (default: UPDATE_GIT_BRANCH, or main)
  --release-tag NAME   Record NAME as the installed release: a push sends its
                       own "git describe" (v1.2.0-5-g5622dcf), so the interface
                       can tell "ahead of v1.2.0" from "v1.2.0"
  --no-restart         Install without restarting the services
  --keep N             Number of backups to keep (default: UPDATE_BACKUP_KEEP)
  --quiet              Only print errors
  --rollback           Restore the most recent backup and exit
  --list-backups       List available backups and exit
  -h, --help           This help
USAGE
}

conf_get() {
    local key="$1" default="${2:-}" value=""
    if [ -f "$ENV_FILE" ]; then
        value="$(env -i bash -c "set -e; source '$ENV_FILE'; printf '%s' \"\${$key:-}\"" 2>/dev/null || true)"
    fi
    [ -n "$value" ] && echo "$value" || echo "$default"
}

ACTION=update
while [ $# -gt 0 ]; do
    case "$1" in
        --source)        SOURCE_DIR="${2:-}"; SOURCE_KIND=usb;  shift 2 ;;
        --from-archive)  ARCHIVE="${2:-}";    SOURCE_KIND=usb;  shift 2 ;;
        --from-git)
            SOURCE_KIND=git
            if [ "${2:-}" ] && [ "${2#-}" = "$2" ]; then GIT_URL="$2"; shift 2; else shift; fi ;;
        --from-release)
            SOURCE_KIND=release
            if [ "${2:-}" ] && [ "${2#-}" = "$2" ]; then RELEASE_TAG="$2"; shift 2; else shift; fi ;;
        --branch)        GIT_BRANCH="${2:-}"; shift 2 ;;
        --release-tag)   RELEASE_STAMP="${2:-}"; shift 2 ;;
        --keep)          BACKUP_KEEP="${2:-3}"; BACKUP_KEEP_SET=1; shift 2 ;;
        --no-restart)    DO_RESTART=0; shift ;;
        --quiet)         QUIET=1; shift ;;
        --rollback)      ACTION=rollback; shift ;;
        --list-backups)  ACTION=list; shift ;;
        -h|--help)       usage; exit 0 ;;
        *)               echo "Unknown option: $1" >&2; usage; exit 2 ;;
    esac
done

if [ -n "$RELEASE_STAMP" ]; then
    printf '%s' "$RELEASE_STAMP" | grep -Eq '^[A-Za-z0-9_.-]+$' \
        || fail "invalid release tag: $RELEASE_STAMP"
fi

if [ "$INSTALL_DIR" = "/opt/rukebox" ] && [ "$EUID" -ne 0 ]; then
    fail "run this script with sudo."
fi

[ "$BACKUP_KEEP_SET" = "1" ] || BACKUP_KEEP="$(conf_get UPDATE_BACKUP_KEEP "$BACKUP_KEEP")"
if [ -z "${RUKEBOX_STATE_DIR:-}" ]; then
    VERSION_FILE="$(conf_get UPDATE_VERSION_FILE "$VERSION_FILE")"
fi
mkdir -p "$BACKUP_DIR"

ALL_SERVICES="rukebox-daemon.service rukebox-web.service flic-bridge.service rukebox-speaker-buttons.service"
RUNNING_SERVICES=""

remember_running() {
    local svc
    for svc in $ALL_SERVICES; do
        if systemctl is-active --quiet "$svc" 2>/dev/null; then
            RUNNING_SERVICES="$RUNNING_SERVICES $svc"
        fi
    done
}

stop_services() {
    local svc
    for svc in $RUNNING_SERVICES; do
        say "   stopping $svc"
        systemctl stop "$svc" || true
    done
}

start_services() {
    local svc
    for svc in $RUNNING_SERVICES; do
        [ "$svc" = "rukebox-web.service" ] && continue
        say "   starting $svc"
        systemctl start "$svc" || echo "WARNING: could not restart $svc" >&2
    done
    case "$RUNNING_SERVICES" in
        *rukebox-web.service*)
            say "   starting rukebox-web.service"
            systemctl start rukebox-web.service || echo "WARNING: could not restart rukebox-web.service" >&2 ;;
    esac
}

list_backups() { ls -1t "$BACKUP_DIR"/opt-rukebox-*.tar.gz 2>/dev/null || true; }

if [ "$ACTION" = "list" ]; then
    backups="$(list_backups)"
    if [ -z "$backups" ]; then
        echo "No backup in $BACKUP_DIR"
    else
        echo "Backups available (most recent first):"
        echo "$backups" | while read -r b; do
            echo "  $(basename "$b")  $(du -h "$b" | cut -f1)"
        done
    fi
    exit 0
fi

record_stat() {
    local src="${INSTALL_DIR}/src/stats.py"
    [ -f "$src" ] || return 0
    RUKEBOX_STATE_DIR="$STATE_DIR" \
        su -s /bin/sh -c "python3 '$src' record '$1' '${2:-}' '${3:-}'" \
        "$RUKEBOX_USER" > /dev/null 2>&1 || true
}

if [ "$ACTION" = "rollback" ]; then
    latest="$(list_backups | head -n1)"
    [ -n "$latest" ] || fail "no backup to restore in $BACKUP_DIR"
    step "Restoring $(basename "$latest")"
    mark_updating
    remember_running
    stop_services
    rm -rf "$INSTALL_DIR.rollback"
    mkdir -p "$INSTALL_DIR.rollback"
    tar xzf "$latest" -C "$INSTALL_DIR.rollback"
    rm -rf "$INSTALL_DIR.discard"
    mv "$INSTALL_DIR" "$INSTALL_DIR.discard"
    mv "$INSTALL_DIR.rollback" "$INSTALL_DIR"
    rm -rf "$INSTALL_DIR.discard"
    chown -R "$RUKEBOX_USER:$RUKEBOX_USER" "$INSTALL_DIR"
    systemctl daemon-reload
    [ "$DO_RESTART" = "1" ] && start_services
    record_stat "update_rolled_back" "$(basename "$latest")"
    say ""
    say "Rolled back. Check: journalctl -u rukebox-daemon.service -n 50"
    exit 0
fi

[ -n "$SOURCE_KIND" ] || { usage; fail "no source given."; }

if [ -n "$ARCHIVE" ]; then
    [ -f "$ARCHIVE" ] || fail "archive not found: $ARCHIVE"
    step "Extracting $ARCHIVE"
    SOURCE_DIR="$(mktemp -d /tmp/rukebox-source-XXXXXX)"
    tar xzf "$ARCHIVE" -C "$SOURCE_DIR"
    if [ ! -d "$SOURCE_DIR/src" ]; then
        inner="$(find "$SOURCE_DIR" -mindepth 1 -maxdepth 1 -type d | head -n1)"
        [ -n "$inner" ] && [ -d "$inner/src" ] && SOURCE_DIR="$inner"
    fi
fi

if [ "$SOURCE_KIND" = "git" ]; then
    command -v git >/dev/null 2>&1 || fail "git is not installed (sudo apt-get install -y git)."
    [ -n "$GIT_URL" ] || GIT_URL="$(conf_get UPDATE_GIT_URL)"
    [ -n "$GIT_URL" ] || fail "no Git URL: pass one, or set git_url under updates: in $CONFIG_FILE."
    [ -n "$GIT_BRANCH" ] || GIT_BRANCH="$(conf_get UPDATE_GIT_BRANCH main)"

    step "Fetching $GIT_URL ($GIT_BRANCH)"
    if [ -d "$GIT_CHECKOUT/.git" ]; then
        current_url="$(git -C "$GIT_CHECKOUT" remote get-url origin 2>/dev/null || echo "")"
        if [ "$current_url" != "$GIT_URL" ]; then
            say "   configured URL changed, re-cloning"
            rm -rf "$GIT_CHECKOUT"
        fi
    fi
    if [ -d "$GIT_CHECKOUT/.git" ]; then
        git -C "$GIT_CHECKOUT" remote set-url origin "$GIT_URL"
        git -C "$GIT_CHECKOUT" fetch --depth 1 origin "$GIT_BRANCH" \
            || fail "fetch failed (no network? see setup_home_wifi.sh)"
        git -C "$GIT_CHECKOUT" checkout -B "$GIT_BRANCH" FETCH_HEAD
    else
        rm -rf "$GIT_CHECKOUT"
        git clone --depth 1 --branch "$GIT_BRANCH" "$GIT_URL" "$GIT_CHECKOUT" \
            || fail "clone failed (no network? see setup_home_wifi.sh)"
    fi
    SOURCE_DIR="$GIT_CHECKOUT"
fi

if [ "$SOURCE_KIND" = "release" ]; then
    command -v curl >/dev/null 2>&1 || fail "curl is not installed (sudo apt-get install -y curl)."
    GITHUB_REPO="$(conf_get UPDATE_GITHUB_REPO Arubinu/Rukebox)"
    { printf '%s' "$GITHUB_REPO" | grep -Eq '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' \
        && ! printf '%s' "$GITHUB_REPO" | grep -Eq '(^|/)\.+(/|$)'; } \
        || fail "invalid GitHub repository: $GITHUB_REPO (expected owner/name)."
    if [ -n "$RELEASE_TAG" ]; then
        printf '%s' "$RELEASE_TAG" | grep -Eq '^[A-Za-z0-9_.-]+$' || fail "invalid release tag: $RELEASE_TAG"
        api="https://api.github.com/repos/$GITHUB_REPO/releases/tags/$RELEASE_TAG"
    else
        api="https://api.github.com/repos/$GITHUB_REPO/releases/latest"
    fi
    step "Looking up the release on github.com/$GITHUB_REPO"
    release_json="$(curl -fsSL --max-time 30 -H 'Accept: application/vnd.github+json' \
        -H 'User-Agent: Rukebox-updater' "$api")" \
        || fail "no release found (no network, or nothing published on github.com/$GITHUB_REPO yet)."
    RELEASE_TAG="$(printf '%s' "$release_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')" \
        || fail "unreadable answer from GitHub."
    printf '%s' "$RELEASE_TAG" | grep -Eq '^[A-Za-z0-9_.-]+$' || fail "unexpected release tag: $RELEASE_TAG"
    step "Downloading $RELEASE_TAG"
    RELEASE_DIR="$(mktemp -d /tmp/rukebox-release-XXXXXX)"
    curl -fsSL --max-time 600 -H 'User-Agent: Rukebox-updater' -o "$RELEASE_DIR/release.tar.gz" \
        "https://codeload.github.com/$GITHUB_REPO/tar.gz/refs/tags/$RELEASE_TAG" \
        || fail "download of $RELEASE_TAG failed."
    mkdir -p "$RELEASE_DIR/tree"
    tar xzf "$RELEASE_DIR/release.tar.gz" -C "$RELEASE_DIR/tree" --strip-components=1 \
        || fail "the $RELEASE_TAG archive could not be extracted."
    SOURCE_DIR="$RELEASE_DIR/tree"
fi

[ -n "$SOURCE_DIR" ] || fail "no source directory."
SOURCE_DIR="$(readlink -f "$SOURCE_DIR")"
[ -d "$SOURCE_DIR" ] || fail "source directory not found: $SOURCE_DIR"
[ "$SOURCE_DIR" != "$INSTALL_DIR" ] || fail "the source cannot be $INSTALL_DIR itself."

mark_updating
step "Checking the new version"
for required in src/rukebox_daemon.py src/web_server.py src/config_and_scan.py \
                src/config_file.py src/config_schema.py src/announcements.py \
                src/playlist.py src/track_order.py src/web_auth.py src/gpio_reset.py \
                src/gpio_click.py src/speaker_buttons.py src/gpio_pins.py src/captive_portal.py src/track_media.py src/suggestions.py src/audio_output.py \
                web/index.html web/i18n.js scripts/install.sh config/rukebox.yaml; do
    [ -f "$SOURCE_DIR/$required" ] || fail "$required missing from $SOURCE_DIR - wrong folder?"
done

if ! python3 -m py_compile "$SOURCE_DIR"/src/*.py 2>/tmp/rukebox-update-pycompile.log; then
    cat /tmp/rukebox-update-pycompile.log >&2
    fail "the new Python code does not compile - nothing was changed."
fi
find "$SOURCE_DIR/src" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
say "   Python compiles"

for script in "$SOURCE_DIR"/scripts/*.sh; do
    [ -f "$script" ] || continue
    bash -n "$script" || fail "syntax error in $(basename "$script") - nothing was changed."
done
say "   shell scripts parse"

NEW_VERSION="$(python3 "$SOURCE_DIR/src/version.py" show "$SOURCE_DIR" 2>/dev/null \
               | sed -n 's/.*"tree_hash_short": "\([^"]*\)".*/\1/p')"
CURRENT_VERSION="$(sed -n 's/.*"tree_hash_short": "\([^"]*\)".*/\1/p' "$VERSION_FILE" 2>/dev/null || true)"
say "   version ${CURRENT_VERSION:-unknown} -> ${NEW_VERSION:-unknown}"
if [ -n "$NEW_VERSION" ] && [ "$NEW_VERSION" = "$CURRENT_VERSION" ]; then
    say ""
    say "Already up to date (${NEW_VERSION}). Nothing to do."
    exit 0
fi

remember_running
[ -n "$RUNNING_SERVICES" ] && say "   services running:$RUNNING_SERVICES"

BACKUP=""
if [ -d "$INSTALL_DIR" ]; then
    step "Backing up the current version"
    BACKUP="$BACKUP_DIR/opt-rukebox-$(date +%Y%m%d-%H%M%S).tar.gz"
    tar czf "$BACKUP" -C "$INSTALL_DIR" . || fail "backup failed, aborting."
    say "   $BACKUP"
fi

restore_backup() {
    [ -n "$BACKUP" ] || return 0
    echo "Restoring the previous version..." >&2
    rm -rf "$INSTALL_DIR.failed"
    [ -d "$INSTALL_DIR" ] && mv "$INSTALL_DIR" "$INSTALL_DIR.failed"
    mkdir -p "$INSTALL_DIR"
    tar xzf "$BACKUP" -C "$INSTALL_DIR"
    chown -R "$RUKEBOX_USER:$RUKEBOX_USER" "$INSTALL_DIR"
    systemctl daemon-reload || true
    start_services
    echo "Previous version restored. Failed attempt kept in $INSTALL_DIR.failed" >&2
}

step "Installing"
stop_services

STAGE="$INSTALL_DIR.new"
rm -rf "$STAGE"
mkdir -p "$STAGE"
if ! {
    cp -r "$SOURCE_DIR/src" "$SOURCE_DIR/scripts" "$SOURCE_DIR/web" "$STAGE/" &&
    mkdir -p "$STAGE/config" &&
    cp "$SOURCE_DIR/config/rukebox.yaml" "$STAGE/config/" &&
    { [ -f "$SOURCE_DIR/config/sudoers-rukebox" ] && cp "$SOURCE_DIR/config/sudoers-rukebox" "$STAGE/config/" || true; } &&
    chmod +x "$STAGE"/scripts/*.sh "$STAGE"/src/*.py
}; then
    rm -rf "$STAGE"
    fail "could not prepare the new version - nothing was changed."
fi

if [ -d "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR.previous"
    mv "$INSTALL_DIR" "$INSTALL_DIR.previous"
fi
if ! mv "$STAGE" "$INSTALL_DIR"; then
    [ -d "$INSTALL_DIR.previous" ] && mv "$INSTALL_DIR.previous" "$INSTALL_DIR"
    fail "swap failed - previous version left in place."
fi
rm -rf "$INSTALL_DIR.previous"
chown -R "$RUKEBOX_USER:$RUKEBOX_USER" "$INSTALL_DIR"
say "   $INSTALL_DIR updated"

trap 'restore_backup; rm -f "$0"' ERR

step "Updating $CONFIG_FILE"
if RESULT="$(python3 - "$INSTALL_DIR/src" "$CONFIG_FILE" "$ENV_FILE" <<'PYENSURE'
import sys
sys.path.insert(0, sys.argv[1])
import config_file
action, count = config_file.ensure_file(sys.argv[2], sys.argv[3])
print(action, count)
PYENSURE
)"; then
    case "$RESULT" in
        "created "*)   say "   config file created" ;;
        "updated "*)   say "   added $(echo "$RESULT" | cut -d' ' -f2) new setting(s), existing values untouched" ;;
        *)             say "   already up to date" ;;
    esac
else
    echo "WARNING: could not update $CONFIG_FILE." >&2
    echo "         The radio will use built-in defaults for anything missing;" >&2
    echo "         compare with $INSTALL_DIR/config/rukebox.yaml when convenient." >&2
fi
chown -R "$RUKEBOX_USER:$RUKEBOX_USER" "$(dirname "$CONFIG_FILE")"

# WirePlumber reads the offered codecs from a drop-in generated from the config.
if [ -f "$INSTALL_DIR/src/bt_codec.py" ]; then
    if python3 "$INSTALL_DIR/src/bt_codec.py" write >/dev/null 2>&1; then
        say "   Bluetooth codec drop-in written"
    else
        echo "WARNING: could not write the Bluetooth codec drop-in." >&2
    fi
fi

if [ -d "$SOURCE_DIR/systemd" ]; then
    step "Updating the systemd units"
    cp "$SOURCE_DIR/systemd/"*.service "$SYSTEMD_DIR/"
    systemctl daemon-reload
    systemctl enable rukebox-config.service 2>/dev/null || true
    systemctl enable rukebox-gpio-reset.service 2>/dev/null || true
    if [ -f "$SOURCE_DIR/scripts/usb_gadget.sh" ]; then
        install -m 755 -o root -g root             "$SOURCE_DIR/scripts/usb_gadget.sh" /usr/local/sbin/rukebox-usb-gadget 2>/dev/null || true
    fi
    if [ -f "$SOURCE_DIR/scripts/install_flic_sdk.sh" ]; then
        install -m 755 -o root -g root "$SOURCE_DIR/scripts/install_flic_sdk.sh" /usr/local/sbin/rukebox-flic-sdk 2>/dev/null || true
    fi
    if [ -f "$SOURCE_DIR/scripts/account_setup.sh" ]; then
        install -m 755 -o root -g root "$SOURCE_DIR/scripts/account_setup.sh" /usr/local/sbin/rukebox-account-setup 2>/dev/null || true
    fi
    systemctl enable rukebox-usb-gadget.service 2>/dev/null || true
    systemctl enable rukebox-act-led.service 2>/dev/null || true
    systemctl enable --now rukebox-bt-radio.service 2>/dev/null || true
    # WirePlumber's Bluetooth monitor waits for an "active" seat a headless Pi never has.
    if [ -f "$SOURCE_DIR/config/wireplumber-bluez.conf" ]; then
        install -D -m 644 -o root -g root "$SOURCE_DIR/config/wireplumber-bluez.conf" \
            /etc/wireplumber/wireplumber.conf.d/10-rukebox-bluez.conf 2>/dev/null || true
    fi
    if [ -f "$SOURCE_DIR/config/rt-limits.conf" ]; then
        install -D -m 644 -o root -g root "$SOURCE_DIR/config/rt-limits.conf" \
            /etc/systemd/system/user@.service.d/10-rukebox-rt.conf 2>/dev/null || true
    fi
    systemctl enable --now rukebox-speaker-buttons.service 2>/dev/null || true
    systemctl enable --now home-wifi-connect.service 2>/dev/null || true
    loginctl enable-linger pi 2>/dev/null || true
fi

if [ -f "$INSTALL_DIR/config/sudoers-rukebox" ]; then
    step "Updating the sudo grants"
    SUDOERS_CANDIDATE="$(mktemp)"
    # sudoers rejects the whole file over one CR (a checkout made on Windows).
    tr -d '\r' < "$INSTALL_DIR/config/sudoers-rukebox" > "$SUDOERS_CANDIDATE"
    if visudo -cqf "$SUDOERS_CANDIDATE" 2>/dev/null; then
        install -m 440 -o root -g root "$SUDOERS_CANDIDATE" "$SUDOERS_FILE"
        say "   $SUDOERS_FILE"
    else
        echo "WARNING: new sudoers file rejected by visudo, keeping the existing one" >&2
    fi
    rm -f "$SUDOERS_CANDIDATE"
fi

if [ -d "$SOURCE_DIR/assets/sounds" ]; then
    mkdir -p "$AUDIO_SYSTEM_DIR"
    for sound in "$SOURCE_DIR"/assets/sounds/*; do
        [ -f "$sound" ] || continue
        target="$AUDIO_SYSTEM_DIR/$(basename "$sound")"
        if [ ! -f "$target" ]; then
            cp "$sound" "$target"
            say "   added sound $(basename "$sound")"
        fi
    done
    chown -R "$RUKEBOX_USER:$RUKEBOX_USER" "$AUDIO_SYSTEM_DIR"
fi

install -m 755 -o root -g root "$INSTALL_DIR/scripts/update.sh" "$UPDATE_WRAPPER"

mkdir -p "$STATE_DIR"
python3 "$INSTALL_DIR/src/version.py" write "$VERSION_FILE" "$SOURCE_DIR" "$SOURCE_KIND" "${RELEASE_STAMP:-$RELEASE_TAG}" >/dev/null
chown -R "$RUKEBOX_USER:$RUKEBOX_USER" "$STATE_DIR"

trap 'rm -f "$0"' ERR

if [ "$DO_RESTART" = "1" ]; then
    step "Restarting the services"
    start_services
else
    say "   --no-restart: services left stopped"
fi

if [ "${BACKUP_KEEP:-3}" -gt 0 ] 2>/dev/null; then
    list_backups | tail -n +$((BACKUP_KEEP + 1)) | while read -r old; do
        rm -f "$old"
    done
fi

record_stat "update_applied" "$SOURCE_KIND" \
    "{\"from\": \"${CURRENT_VERSION:-unknown}\", \"to\": \"${NEW_VERSION:-unknown}\"}"

say ""
say "== Updated to ${NEW_VERSION:-unknown} (source: $SOURCE_KIND) =="
[ -n "$BACKUP" ] && say "Rollback if needed: sudo $UPDATE_WRAPPER --rollback"
say "Check: journalctl -u rukebox-daemon.service -n 50"
