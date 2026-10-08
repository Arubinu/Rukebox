#!/bin/sh
# Creates the configuration on a first start and adds new settings to an older
# one; it is never rewritten.
set -e

# The sound server's socket directory. A volume mounted over /run could take
# it away, and the daemon would then find no output at all.
mkdir -p "${XDG_RUNTIME_DIR:-/run/rukebox}" /config /data
chmod 777 "${XDG_RUNTIME_DIR:-/run/rukebox}" /config /data 2>/dev/null || true

fresh=""
[ -f /config/rukebox.yaml ] || fresh="yes"

python3 /opt/rukebox/src/config_file.py ensure /config/rukebox.yaml /config/rukebox.env

# First start only: afterwards the file is the user's.
if [ -n "$fresh" ]; then
  python3 /opt/rukebox/docker/first_start.py
  python3 /opt/rukebox/src/config_file.py sync /config/rukebox.yaml /config/rukebox.env
fi

exec "$@"
