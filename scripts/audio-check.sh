#!/usr/bin/env bash
# One-shot audio path report, for a cut-out or a doubt about the sound:
# the output, the Bluetooth link, the codec and the bitrate it really carries,
# measured over a few seconds. Same report as the "Audio diagnostic" button.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$HERE/../src/audio_diag.py" report "$@"
