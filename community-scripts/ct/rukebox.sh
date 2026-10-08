#!/usr/bin/env bash

# Copyright (c) 2021-2026 community-scripts ORG
# License: MIT | https://github.com/community-scripts/ProxmoxVE/raw/main/LICENSE
# Source: https://github.com/Arubinu/Rukebox

APP="Rukebox"
var_tags="${var_tags:-media;music}"
var_cpu="${var_cpu:-2}"
var_ram="${var_ram:-1024}"
var_disk="${var_disk:-4}"
var_os="${var_os:-debian}"
var_version="${var_version:-13}"
var_arm64="${var_arm64:-yes}"
var_unprivileged="${var_unprivileged:-1}"
var_install="rukebox-install"

header_info "$APP"
variables
color
catch_errors

function update_script() {
  header_info
  check_container_storage
  check_container_resources

  if [[ ! -d /opt/rukebox ]]; then
    msg_error "No ${APP} installation found!"
    exit
  fi

  if check_for_gh_release "rukebox" "Arubinu/Rukebox"; then
    msg_info "Stopping Rukebox"
    systemctl stop rukebox-daemon.service rukebox-web.service
    msg_ok "Stopped Rukebox"

    # The project's own updater: it backs the tree up, refuses code that does
    # not compile and rolls back by itself if the new version does not come up.
    msg_info "Updating Rukebox"
    $STD /usr/local/sbin/rukebox-update --from-git https://github.com/Arubinu/Rukebox.git
    msg_ok "Updated Rukebox"

    msg_info "Starting Rukebox"
    systemctl start rukebox-daemon.service rukebox-web.service
    msg_ok "Started Rukebox"
    msg_ok "Updated successfully!"
  fi
  exit
}

start
build_container
description

msg_ok "Completed successfully!\n"
echo -e "${CREATING}${GN}${APP} setup has been successfully initialized!${CL}"
echo -e "${INFO}${YW}Open the interface at:${CL}"
echo -e "${GATEWAY}${BGN}http://${IP}${CL}"
echo -e "${INFO}${YW}There is no access point and no password by default: put one on${CL}"
echo -e "${INFO}${YW}from the Security card if anything else can reach this address.${CL}"
