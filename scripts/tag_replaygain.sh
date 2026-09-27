#!/bin/bash

set -euo pipefail

MUSIC_DIR="${1:-}"
TARGET_LUFS="${REPLAYGAIN_TARGET_LUFS:--14}"

if [ -z "$MUSIC_DIR" ]; then
    echo "Usage: $0 /path/to/music" >&2
    exit 1
fi

echo "Tagging ReplayGain (target ${TARGET_LUFS} LUFS) in $MUSIC_DIR ..."
echo "(recursive, subfolders included)"

rsgain custom -O -a -l "$TARGET_LUFS" -r "$MUSIC_DIR"

echo "Done. Quick check of a random file:"
find "$MUSIC_DIR" -type f -name '*.mp3' | head -n 1 | while read -r f; do
    ffprobe -v quiet -show_entries format_tags -of json "$f"
done
