#!/usr/bin/env bash

# Copyright (c) 2021-2026 community-scripts ORG
# License: MIT | https://github.com/community-scripts/ProxmoxVE/raw/main/LICENSE
# Source: https://github.com/Arubinu/Rukebox
#
# Runs inside the container the ct/ script created. It installs Rukebox the
# way the project installs itself, with the container profile: the daemon, the
# web interface, the schedules, the statistics and the updater, and none of
# what a container cannot have (access point, USB gadget, GPIO, hardware
# clock, activity LED). See docs/guide.md, "Running in a container".

source /dev/stdin <<<"$FUNCTIONS_FILE_PATH"
color
verb_ip6
catch_errors
setting_up_container
network_check
update_os

msg_info "Installing Dependencies"
$STD apt install -y \
  ffmpeg \
  mpv \
  python3 \
  python3-pip \
  python3-yaml \
  espeak-ng \
  alsa-utils \
  bluez \
  pipewire \
  pipewire-bin \
  wireplumber \
  pipewire-audio \
  git \
  curl
msg_ok "Installed Dependencies"

msg_info "Installing Rukebox"
$STD git clone --depth 1 https://github.com/Arubinu/Rukebox.git /opt/rukebox-src
# The project's own installer, in its container profile: it checks what it is
# running on, keeps systemd and the units, and leaves out the Pi's boot
# configuration, its RTC overlay and its access point.
$STD env RUKEBOX_PROFILE=lxc RUKEBOX_SYSTEM_UPGRADE=no RUKEBOX_BOOT_TWEAKS=no \
  bash /opt/rukebox-src/scripts/install.sh
rm -rf /opt/rukebox-src
msg_ok "Installed Rukebox"

msg_info "Starting Rukebox"
$STD systemctl enable -q --now rukebox-daemon.service rukebox-web.service
msg_ok "Started Rukebox"

msg_info "Setting up the network output"
# A container has no sound card: the radio plays into a virtual output of its
# own, and this is what you hear - with "Listen here" on the page, in VLC at
# http://<ip>/stream.opus, or on a network speaker.
$STD python3 /opt/rukebox/src/config_file.py set STREAM_ENABLED=true
$STD systemctl restart rukebox-web.service
msg_ok "Network output ready"

motd_ssh
customize
cleanup_lxc
