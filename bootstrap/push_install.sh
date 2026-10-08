#!/bin/bash
set -euo pipefail

HOST="169.254.7.7"
HOST_EXPLICIT=0
USER_NAME="pi"
PORT="22"
IDENTITY=""
REMOTE_TMP="/tmp"
DRY_RUN=0
ASSUME_YES=0

usage() {
    cat <<'USAGE'
Usage: push_install.sh [OPTIONS]

  --host HOST        Pi address (default: 169.254.7.7, the USB cable) -
                      also accepts a hostname (e.g. rukebox.local) or
                      any address reachable over your own network
  --user NAME        SSH account (default: pi)
  --port N           SSH port (default: 22)
  --identity FILE    SSH private key to use (omit to be prompted for a
                      password instead, if the account needs one)
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
        --dry-run)  DRY_RUN=1; shift ;;
        --yes|-y)   ASSUME_YES=1; shift ;;
        -h|--help)  usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
    esac
done

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Rukebox - pushing the first installation ==="
echo ""

for tool in tar ssh scp; do
    command -v "$tool" >/dev/null 2>&1 || { echo "$tool not found on this computer." >&2; exit 1; }
done

for required in src/rukebox_daemon.py web/index.html scripts/install.sh config/rukebox.yaml; do
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
ARCHIVE="$(mktemp -t rukebox-install-XXXXXX).tar.gz"
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
    echo "  - over USB: cable plugged into the Pi's DATA port (not PWR)," >&2
    echo "    Pi powered on and finished booting" >&2
    echo "  - over the network: --host/--user point at the right Pi, and" >&2
    echo "    it already has SSH enabled and reachable (see README," >&2
    echo "    'Installing on a blank SD card')" >&2
    echo "  - ssh $ORIGINAL_TARGET works on its own" >&2
    exit 1
fi

if ssh "${SSH_OPTS[@]}" "$TARGET" 'test -d /opt/rukebox' 2>/dev/null; then
    echo "   Rukebox already appears to be installed there."
    if [ "$ASSUME_YES" != "1" ]; then
        read -rp "Reinstall/repair anyway? Settings, music and statistics are preserved. (y/N) " confirm
        [[ "$confirm" =~ ^[yY] ]] || { echo "Cancelled."; exit 0; }
    fi
elif [ "$ASSUME_YES" != "1" ]; then
    echo ""
    echo "About to install Rukebox on $TARGET."
    read -rp "Continue? (Y/n) " confirm
    [[ "$confirm" =~ ^[nN] ]] && { echo "Cancelled."; exit 0; }
fi

REMOTE_ARCHIVE="$REMOTE_TMP/rukebox-install-$STAMP.tar.gz"
REMOTE_APPLY="$REMOTE_TMP/rukebox-install-apply-$STAMP.sh"
echo ""
echo "== Sending ($SIZE) =="
scp "${SCP_OPTS[@]}" "$ARCHIVE" "$TARGET:$REMOTE_ARCHIVE"

echo ""
echo "== Installing on the Pi (needs the Pi's own Internet access - apt/pip) =="
APPLY_LOCAL="$(mktemp -t rukebox-install-apply-XXXXXX)"
cleanup() { rm -f "$ARCHIVE" "$APPLY_LOCAL"; }
cat > "$APPLY_LOCAL" <<APPLY
set -e
STAGING="\$(mktemp -d /tmp/rukebox-staging-XXXXXX)"
cleanup() {
    # Root-owned __pycache__ may be left here: a failed cleanup must never fail
    # the install, hence "|| true".
    sudo rm -rf "\$STAGING" 2>/dev/null || rm -rf "\$STAGING" 2>/dev/null || true
    rm -f "$REMOTE_ARCHIVE" "$REMOTE_APPLY"
}
trap cleanup EXIT
tar xzf "$REMOTE_ARCHIVE" -C "\$STAGING"
chmod +x "\$STAGING/scripts/"*.sh
sudo "\$STAGING/scripts/install.sh"
APPLY
scp "${SCP_OPTS[@]}" "$APPLY_LOCAL" "$TARGET:$REMOTE_APPLY"

if ! ssh -t "${SSH_OPTS[@]}" "$TARGET" "sh $REMOTE_APPLY"; then
    echo "" >&2
    echo "The installation failed - see the output above." >&2
    exit 1
fi

echo ""
echo "== Done =="
echo "Next steps (see the installer's own output above for the full list):"
echo "  ssh $TARGET"
echo "  sudo nano /etc/rukebox/rukebox.yaml   # speaker MAC, times, folders"
