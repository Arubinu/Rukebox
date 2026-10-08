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
tar czf "$ARCHIVE" -C "$PROJECT_ROOT" \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.DS_Store' \
    --exclude='node_modules' \
    --exclude='*.tar.gz' \
    --exclude='./*.tgz' \
    --exclude='./.venv' \
    --exclude='./graphify-out' \
    --exclude='./src/graphify-out' \
    --exclude='./assets/icons' \
    --exclude='./bootstrap' \
    --exclude='./dist' \
    --exclude='./docs' \
    --exclude='./tests' \
    --exclude='./.claude' \
    --exclude='./.scratch' \
    --exclude='./docker/data' \
    --exclude='./docker/config' \
    --exclude='./CLAUDE.md' \
    --exclude='./TODO.md' \
    --exclude='./README.md' \
    --exclude='./README.*.md' \
    --exclude='./node_modules' \
    --exclude='./package.json' \
    --exclude='./package-lock.json' \
    --exclude='./.github' \
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
    # The USB address only answers over the cable; mDNS also works unplugged.
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

# Bluetooth audio starves the Wi-Fi receiver: the music pauses for the upload.
# Every ssh is retried: a lost answer would leave the music paused.
radio_quiet() {
    local attempt
    attempt=1
    while [ "$attempt" -le 3 ]; do
        if ssh "${SSH_OPTS[@]}" "$TARGET" "python3 - $1" <<'PY'
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
        then
            return 0
        fi
        attempt=$((attempt + 1))
        if [ "$attempt" -le 3 ]; then
            sleep 6
        fi
    done
    return 1
}

PAUSED_BY_US=""
if [ "$KEEP_PLAYING" != "1" ]; then
    QUIET="$(radio_quiet pause || true)"
    if [ "$QUIET" = "paused" ]; then
        PAUSED_BY_US=1
        echo "Music paused for the transfer; the daemon brings it back."
    fi
fi

REMOTE_ARCHIVE="$REMOTE_TMP/rukebox-update-$STAMP.tar.gz"
REMOTE_APPLY="$REMOTE_TMP/rukebox-apply-$STAMP.sh"

if [ "$PAUSED_BY_US" = "1" ]; then
    PROBE="$(mktemp -t rukebox-probe-XXXXXX)"
    head -c 32768 /dev/urandom > "$PROBE"
    attempt=1
    while [ "$attempt" -le 5 ]; do
        started="$(date +%s)"
        scp "${SCP_OPTS[@]}" "$PROBE" "$TARGET:/tmp/rukebox-probe.bin" >/dev/null 2>&1 || true
        waited=$(( $(date +%s) - started ))
        if [ "$waited" -lt 6 ]; then
            break
        fi
        echo "   the link is still slow (${waited}s for 32 KB), waiting ..."
        sleep 5
        attempt=$((attempt + 1))
    done
    rm -f "$PROBE"
fi

echo ""
echo "== Sending ($SIZE) =="
if ! scp "${SCP_OPTS[@]}" "$ARCHIVE" "$TARGET:$REMOTE_ARCHIVE"; then
    # One retry: a transfer that dies mid-way is usually the link dropping for a moment.
    echo "   transfer interrupted, trying once more ..."
    sleep 3
    if ! scp "${SCP_OPTS[@]}" "$ARCHIVE" "$TARGET:$REMOTE_ARCHIVE"; then
        # The update will not run, so nothing else will resume the music.
        if [ "$PAUSED_BY_US" = "1" ]; then
            radio_quiet resume >/dev/null 2>&1 || true
        fi
        echo "Transfer failed. The music has been resumed; run this again when the link is back." >&2
        exit 1
    fi
fi

echo ""
echo "== Updating on the Pi =="
# Stamped on the Pi so the Update card can tell "ahead of v1.2.0" from "v1.2.0".
RELEASE_STAMP=""
if command -v git >/dev/null 2>&1; then
    DESCRIBED="$(git -C "$PROJECT_ROOT" describe --tags --always --dirty 2>/dev/null || true)"
    case "$DESCRIBED" in
        ''|*[!A-Za-z0-9_.-]*) ;;
        *) RELEASE_STAMP="$DESCRIBED" ;;
    esac
fi
if [ -n "$RELEASE_STAMP" ]; then
    echo "   stamping: $RELEASE_STAMP"
    EXTRA_UPDATE_ARGS="$EXTRA_UPDATE_ARGS --release-tag $RELEASE_STAMP"
fi
# The cable's own address is link-local: anything else went over the network,
# and the version the Pi records says which.
case "$TARGET" in
    *@169.254.*) SOURCE_KIND=usb ;;
    *)           SOURCE_KIND=wifi ;;
esac
echo "   carried over: $SOURCE_KIND"
EXTRA_UPDATE_ARGS="$EXTRA_UPDATE_ARGS --source-kind $SOURCE_KIND"
# Sent as its own script: a multi-line command quoted through three shells breaks per platform.
# Run the updater from the archive, so a new version can change the update procedure itself.
APPLY_LOCAL="$(mktemp -t rukebox-apply-XXXXXX)"
cleanup() { rm -f "$ARCHIVE" "$APPLY_LOCAL"; }
cat > "$APPLY_LOCAL" <<APPLY
set -e
STAGING="\$(mktemp -d /tmp/rukebox-staging-XXXXXX)"
cleanup() {
    # Root-owned __pycache__ may be left here: a failed cleanup must never fail
    # the update, hence "|| true".
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
    [ "$PAUSED_BY_US" = "1" ] && radio_quiet resume >/dev/null 2>&1 || true
    echo "" >&2
    echo "The update failed. The Pi restored its previous version by itself." >&2
    echo "Details:  ssh $TARGET 'journalctl -u rukebox-daemon.service -n 50'" >&2
    exit 1
fi

# Only when the update did not run: it restarts the daemon, which comes back playing on its own.
[ "$PAUSED_BY_US" = "1" ] && radio_quiet resume >/dev/null 2>&1 || true

echo ""
echo "== Done =="
echo "The Pi now runs ${LOCAL_VERSION:-the pushed version}."
echo ""
echo "Checks:"
echo "  ssh $TARGET 'systemctl status rukebox-daemon.service'"
echo "  Web interface: the Statistics card shows the installed version."
echo ""
echo "To go back:  ssh $TARGET 'sudo /usr/local/sbin/rukebox-update --rollback'"
