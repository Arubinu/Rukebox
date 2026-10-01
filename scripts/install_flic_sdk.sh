#!/bin/bash
set -euo pipefail

DEST="${RUKEBOX_FLIC_SDK_DIR:-/opt/fliclib-linux-hci}"
URL="https://codeload.github.com/50ButtonsEach/fliclib-linux-hci/tar.gz/refs/heads/master"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

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
echo "Flic SDK installed in $DEST"
