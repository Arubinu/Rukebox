# Rukebox in a container: Flask, mpv, ffmpeg and PipeWire, no systemd.
#
# The same source tree a Pi installs to /opt/rukebox, with the four roots
# moved by the environment (see src/paths.py): /config for the YAML and the
# JSON it manages, /data for the statistics and the library, /music for the
# audio. Not a multi-stage build: the tree is small, and what has to be
# installed is the apt packages, not a compiler.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    RUKEBOX_PLATFORM=docker \
    RUKEBOX_INSTALL_DIR=/opt/rukebox \
    RUKEBOX_CONFIG_DIR=/config \
    RUKEBOX_STATE_DIR=/data \
    RUKEBOX_MUSIC_DIR=/music \
    XDG_RUNTIME_DIR=/run/rukebox \
    PIPEWIRE_RUNTIME_DIR=/run/rukebox \
    AUDIO_OUTPUT=docker

# mpv plays the music, ffmpeg encodes the network stream, pipewire and
# wireplumber are the sound server the rest of the project already talks to,
# and espeak-ng is what says the announcements (src/speech.py falls back to it
# for every language). bluez and dbus are for the variant that drives the
# host's Bluetooth.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        mpv \
        pipewire \
        pipewire-pulse \
        wireplumber \
        pulseaudio-utils \
        libasound2-plugins \
        espeak-ng \
        alsa-utils \
        bluez \
        dbus \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# pico2wave is the nicer French voice and not in every Debian: its absence is
# not a reason to fail a build, since speech.py tries it first and then uses
# espeak-ng.
RUN apt-get update \
    && (apt-get install -y --no-install-recommends libttspico-utils || true) \
    && rm -rf /var/lib/apt/lists/*

# The two Python packages the Pi gets from apt; nothing else is installed, so
# the dependency rule in CLAUDE.md still holds literally.
RUN pip install --no-cache-dir flask pyyaml

WORKDIR /opt/rukebox
COPY src/ ./src/
COPY web/ ./web/
COPY config/ ./config/
COPY assets/ ./assets/
COPY docker/ ./docker/

# The virtual output a container has instead of a sound card: without it there
# is no audio node at all, and so nothing to stream. See the file itself.
RUN mkdir -p /etc/pipewire/pipewire.conf.d \
    && cp /opt/rukebox/docker/pipewire-container.conf /etc/pipewire/pipewire.conf.d/

# The configuration is the one the checkout documents. It is NOT baked in: a
# first start writes it from the roots below (src/config_file.py ensure, in
# docker/entrypoint.sh), so what the container documents is the container's own
# paths. An existing /config volume is only ever added to.
#
# Everything here runs as root, inside the container only: it has to bind port
# 80, and the sound server socket lives in a directory only its own user may
# write to. The image is the boundary, not a user inside it.
RUN mkdir -p /config /data /music /run/rukebox && chmod 777 /config /data /run/rukebox \
    && chmod +x /opt/rukebox/docker/entrypoint.sh

VOLUME ["/config", "/data", "/music"]
EXPOSE 80

ENTRYPOINT ["/opt/rukebox/docker/entrypoint.sh"]
CMD ["python3", "/opt/rukebox/docker/supervisor.py"]
