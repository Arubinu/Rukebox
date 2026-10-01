#!/usr/bin/env bash
# Same report as the web interface's "Audio diagnostic" button.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$HERE/../src/audio_diag.py" report "$@"
