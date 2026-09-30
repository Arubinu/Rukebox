#!/bin/bash
set -euo pipefail

HOST="169.254.7.7"
HOST_EXPLICIT=0
USER_NAME="pi"
PORT="22"
IDENTITY=""
REMOTE_TMP="/tmp"
EXTRA_UPDATE_ARGS=""
DRY_RUN=0
ASSUME_YES=0
KEEP_PLAYING=0

usage() {
    cat <<'USAGE'
Usage: push_update.sh [OPTIONS]

  --host HOST        Pi address (default: 169.254.7.7, the USB cable)
  --user NAME        SSH account (default: pi)
  --port N           SSH port (default: 22)
  --identity FILE    SSH private key to use
  --no-restart       Install without restarting the services on the Pi
  --keep-playing     Don't pause the music for the transfer
  --dry-run          Show what would be sent, change nothing
  --yes              Don't ask for confirmation
  -h, --help         This help
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --host)     HOST="${2:-}"; HOST_EXPLICIT=1; shift 2 ;;
        --user)     USER_NAME="${2:-}"; shift 2 ;;
        --port)     PORT="${2:-}"; shift 2 ;;
        --identity) IDENTITY="${2:-}"; shift 2 ;;
        --no-restart) EXTRA_UPDATE_ARGS="$EXTRA_UPDATE_ARGS --no-restart"; shift ;;
        --keep-playing) KEEP_PLAYING=1; shift ;;
        --dry-run)  DRY_RUN=1; shift ;;
        --yes|-y)   ASSUME_YES=1; shift ;;
        -h|--help)  usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
    esac
done

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Rukebox - pushing an update over USB ==="
echo ""

for tool in tar ssh scp; do
    command -v "$tool" >/dev/null 2>&1 || { echo "$tool not found on this computer." >&2; exit 1; }
done

for required in src/rukebox_daemon.py web/index.html scripts/update.sh config/rukebox.yaml; do
    [ -f "$PROJECT_ROOT/$required" ] || {
        echo "$required missing from $PROJECT_ROOT - run this script from the project." >&2
        exit 1
    }
done

SSH_OPTS=(-o ConnectTimeout=10 -p "$PORT")
[ -n "$IDENTITY" ] && SSH_OPTS+=(-i "$IDENTITY")
SCP_OPTS=(-o ConnectTimeout=10 -P "$PORT")
[ -n "$IDENTITY" ] && SCP_OPTS+=(-i "$IDENTITY")
TARGET="$USER_NAME@$HOST"

STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="$(mktemp -t rukebox-update-XXXXXX).tar.gz"
cleanup() { rm -f "$ARCHIVE"; }
trap cleanup EXIT

echo "== Packing $PROJECT_ROOT =="
# What an update can install is src/, scripts/, web/, config/, systemd/ and
# assets/sounds/ (see src/version.py): everything else here is the repository
# itself - the docs, the icon sources, the card-setup build, the tests - and
# sending it made every push carry ~23 MB that the Pi never looks at.
tar czf "$ARCHIVE" -C "$PROJECT_ROOT" \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.DS_Store' \
    --exclude='node_modules' \
    --exclude='*.tar.gz' \
    --exclude='./.venv' \
    --exclude='./graphify-out' \
    --exclude='./src/graphify-out' \
    --exclude='./assets/icons' \
    --exclude='./bootstrap' \
    --exclude='./dist' \
    --exclude='./docs' \
    --exclude='./tests' \
    --exclude='./.claude' \
    --exclude='./CLAUDE.md' \
    --exclude='./TODO.md' \
    --exclude='./README.md' \
    --exclude='./README.*.md' \
    .
SIZE="$(du -h "$ARCHIVE" | cut -f1)"
echo "   archive: $SIZE"

if command -v python3 >/dev/null 2>&1; then
    LOCAL_VERSION="$(python3 "$PROJECT_ROOT/src/version.py" show "$PROJECT_ROOT" 2>/dev/null \
                     | sed -n 's/.*"tree_hash_short": "\([^"]*\)".*/\1/p' || true)"
    [ -n "$LOCAL_VERSION" ] && echo "   version: $LOCAL_VERSION"
fi

if [ "$DRY_RUN" = "1" ]; then
    echo ""
    echo "--dry-run: nothing sent. Content that would be pushed:"
    tar tzf "$ARCHIVE" | sed 's/^/   /' | head -40
    echo "   ..."
    exit 0
fi

# --- Reach the Pi -----------------------------------------------------
# Returns 0 if $1 (a user@host target) answers SSH, printing a hint first
# if a non-interactive probe suggests a password/passphrase is needed.
try_reach() {
    if ! ssh "${SSH_OPTS[@]}" -o BatchMode=yes "$1" true 2>/dev/null; then
        echo "   (interactive authentication)"
    fi
    ssh "${SSH_OPTS[@]}" "$1" true 2>/dev/null
}

echo ""
echo "== Connecting to $TARGET =="
ORIGINAL_TARGET="$TARGET"
REACHED=0
if try_reach "$TARGET"; then
    REACHED=1
elif [ "$HOST_EXPLICIT" != "1" ] && [ "$HOST" != "rukebox.local" ]; then
    # Default target is the fixed USB address, which only works over the
    # cable - fall back to the mDNS hostname so this also works unplugged,
    # on the same Wi-Fi network, without having to pass --host by hand.
    FALLBACK_TARGET="$USER_NAME@rukebox.local"
    echo "   $TARGET not reachable, trying $FALLBACK_TARGET (mDNS) ..."
    if try_reach "$FALLBACK_TARGET"; then
        HOST="rukebox.local"
        TARGET="$FALLBACK_TARGET"
        REACHED=1
    fi
fi

if [ "$REACHED" != "1" ]; then
    echo "" >&2
    if [ "$TARGET" != "$ORIGINAL_TARGET" ]; then
        echo "Could not reach $ORIGINAL_TARGET or $TARGET over SSH." >&2
    else
        echo "Could not reach $TARGET over SSH." >&2
    fi
    echo "" >&2
    echo "Checks:" >&2
    echo "  - USB cable plugged into the Pi's DATA port (not PWR)" >&2
    echo "  - Pi powered on and finished booting" >&2
    echo "  - ssh $ORIGINAL_TARGET works on its own" >&2
    exit 1
fi

if ! ssh "${SSH_OPTS[@]}" "$TARGET" 'test -d /opt/rukebox'; then
    echo "" >&2
    echo "$TARGET is reachable, but Rukebox isn't installed there (/opt/rukebox missing)." >&2
    echo "For a first installation, see README, 'Installing on a blank SD card'," >&2
    echo "or use bootstrap/push_install.sh." >&2
    exit 1
fi

REMOTE_VERSION="$(ssh "${SSH_OPTS[@]}" "$TARGET" \
    "sed -n 's/.*\"tree_hash_short\": \"\([^\"]*\)\".*/\1/p' /var/lib/rukebox/version.json 2>/dev/null" || true)"
echo "   installed version: ${REMOTE_VERSION:-unknown}"
if [ -n "$REMOTE_VERSION" ] && [ "$REMOTE_VERSION" = "${LOCAL_VERSION:-}" ]; then
    echo ""
    echo "The Pi already runs this exact version. Nothing to do."
    exit 0
fi

if [ "$ASSUME_YES" != "1" ]; then
    echo ""
    echo "About to update $TARGET to ${LOCAL_VERSION:-this version}."
    echo "Music, settings and statistics on the Pi are preserved."
    read -rp "Continue? (Y/n) " confirm
    [[ "$confirm" =~ ^[nN] ]] && { echo "Cancelled."; exit 0; }
fi

# --- Send and apply ---------------------------------------------------
# Bluetooth audio from the USB dongle desensitises the Pi's own Wi-Fi
# receiver: measured 2026-09-30, the archive uploads at ~590 KB/s with the
# music paused and at ~3 KB/s while it plays, because the access point drops
# the Pi to 1 Mbit/s with 20% of the large packets lost. Pausing for the
# transfer is what turns a six-minute push back into seconds, so the radio is
# quieted here - and only a pause this script asked for is undone.
radio_quiet() {
    ssh "${SSH_OPTS[@]}" "$TARGET" "python3 - $1" <<'PY'
import sys
sys.path.insert(0, "/opt/rukebox/src")
from control_client import send_control_command

sock = "/tmp/rukebox_control.sock"
action = sys.argv[1]
data = send_control_command(sock, "get_status").get("data") or {}
if action == "pause" and data.get("mode") == "music" and not data.get("paused"):
    ok = send_control_command(sock, "toggle_pause", source="push").get("ok")
    print("paused" if ok else "failed")
if action == "resume" and data.get("paused"):
    ok = send_control_command(sock, "toggle_pause", source="push").get("ok")
    print("resumed" if ok else "failed")
PY
}

PAUSED_BY_US=""
if [ "$KEEP_PLAYING" != "1" ]; then
    QUIET="$(radio_quiet pause || true)"
    if [ "$QUIET" = "paused" ]; then
        PAUSED_BY_US=1
        echo "Music paused for the transfer (--keep-playing to skip)."
    fi
fi

REMOTE_ARCHIVE="$REMOTE_TMP/rukebox-update-$STAMP.tar.gz"
REMOTE_APPLY="$REMOTE_TMP/rukebox-apply-$STAMP.sh"
echo ""
echo "== Sending ($SIZE) =="
if ! scp "${SCP_OPTS[@]}" "$ARCHIVE" "$TARGET:$REMOTE_ARCHIVE"; then
    [ "$PAUSED_BY_US" = "1" ] && radio_quiet resume >/dev/null 2>&1 || true
    echo "Transfer failed." >&2
    exit 1
fi
[ "$PAUSED_BY_US" = "1" ] && radio_quiet resume >/dev/null 2>&1 || true

echo ""
echo "== Updating on the Pi =="
# The steps to run on the Pi are sent as their own little script rather
# than squeezed into the ssh command line: quoting a multi-line command
# through a local shell, ssh, and the remote shell is exactly the kind of
# thing that breaks differently on every platform. It also keeps this
# script and push_update.ps1 doing literally the same thing.
#
# The updater used is the one from the ARCHIVE, not the copy already
# installed, so a new version is free to change the update procedure
# itself.
APPLY_LOCAL="$(mktemp -t rukebox-apply-XXXXXX)"
cleanup() { rm -f "$ARCHIVE" "$APPLY_LOCAL"; }
cat > "$APPLY_LOCAL" <<APPLY
set -e
STAGING="\$(mktemp -d /tmp/rukebox-staging-XXXXXX)"
cleanup() {
    # update.sh just ran under sudo and may have left root-owned files
    # in here (e.g. __pycache__/*.pyc from importing the staged Python
    # source) that this script, running as the plain SSH user, cannot
    # remove - that must never be mistaken for the update itself
    # failing, which is why every branch below ends in "|| true".
    sudo rm -rf "\$STAGING" 2>/dev/null || rm -rf "\$STAGING" 2>/dev/null || true
    rm -f "$REMOTE_ARCHIVE" "$REMOTE_APPLY"
}
trap cleanup EXIT
tar xzf "$REMOTE_ARCHIVE" -C "\$STAGING"
chmod +x "\$STAGING/scripts/update.sh"
sudo "\$STAGING/scripts/update.sh" --source "\$STAGING"$EXTRA_UPDATE_ARGS
APPLY
scp "${SCP_OPTS[@]}" "$APPLY_LOCAL" "$TARGET:$REMOTE_APPLY"

# -t: the updater runs under sudo and may need to ask for a password.
if ! ssh -t "${SSH_OPTS[@]}" "$TARGET" "sh $REMOTE_APPLY"; then
    echo "" >&2
    echo "The update failed. The Pi restored its previous version by itself." >&2
    echo "Details:  ssh $TARGET 'journalctl -u rukebox-daemon.service -n 50'" >&2
    exit 1
fi

echo ""
echo "== Done =="
echo "The Pi now runs ${LOCAL_VERSION:-the pushed version}."
echo ""
echo "Checks:"
echo "  ssh $TARGET 'systemctl status rukebox-daemon.service'"
echo "  Web interface: the Statistics card shows the installed version."
echo ""
echo "To go back:  ssh $TARGET 'sudo /usr/local/sbin/rukebox-update --rollback'"
