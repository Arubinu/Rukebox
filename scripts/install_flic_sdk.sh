#!/bin/bash
set -euo pipefail

DEST="${RUKEBOX_FLIC_SDK_DIR:-/opt/fliclib-linux-hci}"
# A fixed commit, not "master": flicd runs as root, so what is installed must be what was looked at.
REF="${RUKEBOX_FLIC_SDK_REF:-f96d7a8658ba762d811c5f73ed0ed5be6c8c2cf1}"
URL="https://codeload.github.com/50ButtonsEach/fliclib-linux-hci/tar.gz/$REF"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

command -v curl > /dev/null 2>&1 || { echo "curl is missing: apt-get install curl" >&2; exit 1; }
curl -fsSL --max-time 180 "$URL" -o "$TMP/sdk.tar.gz"
mkdir -p "$TMP/sdk"
tar -xzf "$TMP/sdk.tar.gz" -C "$TMP/sdk" --strip-components=1
if [ ! -f "$TMP/sdk/clientlib/python/fliclib.py" ]; then
    echo "Unexpected archive content, nothing installed." >&2
    exit 1
fi

rm -rf "$DEST.new"
mv "$TMP/sdk" "$DEST.new"
rm -rf "$DEST"
mv "$DEST.new" "$DEST"
chmod -R a+rX "$DEST"
chmod 755 "$DEST"/bin/*/flicd 2>/dev/null || true
echo "Flic SDK ($REF) installed in $DEST"
