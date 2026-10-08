#!/usr/bin/env bash

# Copyright (c) 2021-2026 community-scripts ORG
# License: MIT | https://github.com/community-scripts/ProxmoxVE/raw/main/LICENSE
# Source: https://github.com/Arubinu/Rukebox

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
# The project's own installer, in its container profile.
$STD env RUKEBOX_PROFILE=lxc RUKEBOX_SYSTEM_UPGRADE=no RUKEBOX_BOOT_TWEAKS=no \
  bash /opt/rukebox-src/scripts/install.sh
rm -rf /opt/rukebox-src
msg_ok "Installed Rukebox"

msg_info "Starting Rukebox"
$STD systemctl enable -q --now rukebox-daemon.service rukebox-web.service
msg_ok "Started Rukebox"

msg_info "Setting up the network output"
# No sound card: the radio is heard through its network stream.
$STD python3 /opt/rukebox/src/config_file.py set STREAM_ENABLED=true
$STD systemctl restart rukebox-web.service
msg_ok "Network output ready"

motd_ssh
customize
cleanup_lxc
