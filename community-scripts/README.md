# Proxmox community-scripts (proposal, not yet submitted)

Three files, written to the
[community-scripts](https://github.com/community-scripts/ProxmoxVE) rules on
purpose: the day this is proposed upstream, they are copied rather than
rewritten.

```
ct/rukebox.sh              creates the LXC container
install/rukebox-install.sh runs inside it
json/rukebox.json          the fiche the menu reads
```

## How to try them today

They are hosted here, not upstream, so they are run directly from this
repository:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Arubinu/Rukebox/main/community-scripts/ct/rukebox.sh)"
```

On a Proxmox host, as root. The container is created with 2 cores, 1 GB of
RAM and 4 GB of disk on Debian 13, then `install/rukebox-install.sh` runs
inside it and installs Rukebox with `RUKEBOX_PROFILE=lxc` — the project's own
installer, in its container profile (see `docs/guide.md`, "Running in a
container").

`update_script()` calls `/usr/local/sbin/rukebox-update`, the project's own
updater: it backs the tree up, refuses code that does not compile and rolls
back by itself if the new version does not come up. That is what
`rukebox-update` already does over SSH; this only gives it a button in the
Proxmox menu.

## What is deliberately not here

- **No access point, no USB gadget, no GPIO, no hardware clock, no activity
  LED.** A container has none of them, and `src/platform.py` reaches the same
  conclusion at run time: the interface hides those cards and the routes
  behind them answer `unsupported_here`.
- **No Bluetooth of its own.** The daemon can drive the host's BlueZ through
  the D-Bus socket, but the ct/ script does not pass it yet: it needs a
  decision on the host side (the container must be allowed the bus, and the
  host must have a free controller). Left for the review.
- **No music.** The library has to be mounted or copied in; the notes in the
  JSON say so.

## Before submitting upstream

1. Test it on a real Proxmox host, and on a real one at that: the container
   profile has so far been exercised in a Debian container with systemd, not
   in LXC on Proxmox.
2. Follow their
   [contribution guidelines](https://community-scripts.org/contributing/getting-started)
   at the time of the proposal: file layout, `header_info`, the JSON schema
   and the review checklist change.
3. Ask for a category and a logo (the JSON points at `assets/icons/logo.png`
   in this repository).
