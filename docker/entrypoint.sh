#!/bin/sh
# What the container does before anything starts: make sure there IS a
# configuration, and that an older one has every setting this version knows.
#
# The roots are the container's own (see the Dockerfile), so the file this
# writes names /config, /data and /music - a first start needs no prepared
# volume, and an existing one is only ever added to.
set -e

# The sound server's socket directory. A volume mounted over /run could take
# it away, and the daemon would then find no output at all.
mkdir -p "${XDG_RUNTIME_DIR:-/run/rukebox}" /config /data
chmod 777 "${XDG_RUNTIME_DIR:-/run/rukebox}" /config /data 2>/dev/null || true

fresh=""
[ -f /config/rukebox.yaml ] || fresh="yes"

python3 /opt/rukebox/src/config_file.py ensure /config/rukebox.yaml /config/rukebox.env

# Just created: it is the Pi's template, and this machine is not a Pi. Only on
# that first start - afterwards the file is the user's, and the interface is
# the only thing that may change it.
if [ -n "$fresh" ]; then
  python3 /opt/rukebox/docker/first_start.py
  python3 /opt/rukebox/src/config_file.py sync /config/rukebox.yaml /config/rukebox.env
fi

exec "$@"
