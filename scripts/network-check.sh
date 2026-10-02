#!/usr/bin/env bash
# Same report as the web interface's "Network diagnostic" button.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$HERE/../src/net_diag.py" report "$@"
