#!/bin/bash
set -euo pipefail

# Installs Piper and one voice under the state root, which an update keeps.
#   rukebox-piper              the voice of the spoken language (see speech.py)
#   rukebox-piper fr_FR-tom-medium   one named voice
STATE_DIR="${RUKEBOX_STATE_DIR:-/var/lib/rukebox}"
DEST="${RUKEBOX_PIPER_DIR:-$STATE_DIR/piper}"
RELEASE="${RUKEBOX_PIPER_RELEASE:-2023.11.14-2}"
VOICES_URL="${RUKEBOX_PIPER_VOICES_URL:-https://huggingface.co/rhasspy/piper-voices/resolve/main}"
SRC="${RUKEBOX_SRC:-/opt/rukebox/src}"

command -v curl > /dev/null 2>&1 || { echo "curl is missing: apt-get install curl" >&2; exit 1; }

voice="${1:-}"
if [ -z "$voice" ]; then
    language="$(python3 "$SRC/config_file.py" show | awk '$1 == "SPEECH_LANGUAGE" { print $2 }')"
    voice="$(python3 -c "
import sys
sys.path.insert(0, '$SRC')
import speech
print(speech.default_voice('$language'))
")"
fi
[ -n "$voice" ] || { echo "No Piper voice for that language." >&2; exit 1; }

case "$(uname -m)" in
    aarch64|arm64)  asset=piper_linux_aarch64.tar.gz ;;
    armv7l|armv6l)  asset=piper_linux_armv7l.tar.gz ;;
    x86_64|amd64)   asset=piper_linux_x86_64.tar.gz ;;
    *) echo "No Piper build for $(uname -m)." >&2; exit 1 ;;
esac

mkdir -p "$DEST"
if [ ! -x "$DEST/piper" ]; then
    echo "Downloading Piper ($asset)..."
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    curl -fsSL --max-time 900 \
        "https://github.com/rhasspy/piper/releases/download/$RELEASE/$asset" -o "$tmp/piper.tar.gz"
    tar -xzf "$tmp/piper.tar.gz" -C "$DEST" --strip-components=1
    rm -rf "$tmp"
    trap - EXIT
    chmod 755 "$DEST/piper"
fi

# fr_FR-siwis-medium is fr/fr_FR/siwis/medium on the voices host.
iso="${voice%%_*}"
tail_part="${voice#*-}"
name="${tail_part%-*}"
quality="${tail_part##*-}"
where="$iso/${voice%%-*}/$name/$quality"
for suffix in .onnx .onnx.json; do
    if [ ! -s "$DEST/$voice$suffix" ]; then
        echo "Downloading $voice$suffix..."
        curl -fsSL --max-time 1800 "$VOICES_URL/$where/$voice$suffix" -o "$DEST/$voice$suffix"
    fi
done
chmod 644 "$DEST"/*.onnx "$DEST"/*.onnx.json 2>/dev/null || true

python3 "$SRC/config_file.py" set "PIPER_VOICE=$voice" > /dev/null
echo "Piper installed in $DEST ($(du -sh "$DEST" | cut -f1)), saying things with $voice."
