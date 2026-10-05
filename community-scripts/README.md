# Proxmox community-scripts (prepared, not submitted yet)

Two scripts, written to the
[community-scripts](https://github.com/community-scripts/ProxmoxVE) rules so
that the proposal is a copy rather than a rewrite:

```
ct/rukebox.sh              creates the LXC container
install/rukebox-install.sh runs inside it
json/rukebox.json          a draft of the metadata (see "Metadata" below)
```

## Where a new script actually goes

Read from their CONTRIBUTING.md on 2026-10-05, because it decides the whole
plan: **a brand-new script is not submitted to
[ProxmoxVE](https://github.com/community-scripts/ProxmoxVE) at all.**
A pull request with a new script there is closed without review. The path is
[DevScripts](https://github.com/community-scripts/DevScripts), their testing
repository: fork it, add `ct/rukebox.sh` and `install/rukebox-install.sh`,
test it against a real Proxmox instance, open the PR there, and the
maintainers promote it to ProxmoxVE once it has been verified.

So the plan's step 5 is: **test it locally, then open a PR against
DevScripts** — not against ProxmoxVE.

## Metadata is not a file any more

Their CONTRIBUTING.md still mentions JSON files under `json/`, but the
current repository has no such directory (checked through the GitHub API on
2026-10-05: the top level holds `ct/`, `install/`, `misc/`, `tools/`,
`turnkey/`, `vm/` and the documentation). The website's metadata — name,
description, logo, tags, category, and the `app_vars` fields its form offers
— is managed through the website itself (PocketBase), and their rule is
explicit: *"Do not submit metadata changes via repo files."*

`json/rukebox.json` is therefore kept as a **draft of the fields**, useful
when filling that form, and not as a file to copy. The schema follows what
`json/*.json` held before the move (`name`, `slug`, `categories`, `type`,
`updateable`, `privileged`, `interface_port`, `documentation`, `website`,
`logo`, `description`, `install_methods`, `default_credentials`, `notes`).
Two things in it need a human at submission time:

- `categories`: `8` is a placeholder. Ask which category a radio belongs to.
- `logo`: it points at `assets/icons/logo.png` in this repository, which has
  to be published somewhere their website can fetch it from.

## How to try them today

They are hosted here and not upstream, so they are run directly from this
repository:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Arubinu/Rukebox/main/community-scripts/ct/rukebox.sh)"
```

On a Proxmox host, as root. The container is created with 2 cores, 1 GB of
RAM and 4 GB of disk on Debian 13, then `install/rukebox-install.sh` runs
inside it and installs Rukebox with `RUKEBOX_PROFILE=lxc` — the project's own
installer, in its container profile (see `docs/guide.md`, "Running in an
LXC container").

`update_script()` calls `/usr/local/sbin/rukebox-update`, the project's own
updater: it backs the tree up, refuses code that does not compile and rolls
back by itself if the new version does not come up. That is what
`rukebox-update` already does over SSH; this only gives it a button in the
Proxmox menu.

## Why there is no prompt

Their rule: *"Never prompt without an escape hatch"* — an install script that
only asks cannot be deployed unattended. Rukebox's installer already reads
every one of its answers from the environment (`RUKEBOX_PROFILE`,
`RUKEBOX_WEB_PASSWORD`, `RUKEBOX_USER`, `RUKEBOX_AUDIO_ROOT`…) and skips the
question when it finds one, so `install/rukebox-install.sh` exports what it
wants and never blocks. Anything worth offering in their form would be
declared as `var_*` variables on the website record, the way
`app_vars` in the JSON draft shows.

## What is deliberately not here

- **No access point, no USB gadget, no GPIO, no hardware clock, no activity
  LED.** A container has none of them, and `src/platform.py` reaches the same
  conclusion at run time: the interface hides those cards and the routes
  behind them answer `unsupported_here`.
- **No Bluetooth of its own.** The daemon can drive the host's BlueZ through
  the D-Bus socket, but the `ct/` script does not pass it yet: it needs a
  decision on the host side (the container must be allowed the bus, and the
  host must have a free controller). Left for the review.
- **No music.** The library has to be mounted or copied in; the notes in the
  JSON draft say so.

## Before submitting to DevScripts

1. **Test it on a real Proxmox host.** So far the container profile has been
   exercised in a Debian container with systemd (the installer completes, the
   services are enabled, the paths are right, `src/platform.py` reports
   `lxc`), and the Docker image has been run for real — but not LXC on
   Proxmox, which is the only thing that matters here.
2. Re-read their CONTRIBUTING.md on that day: the file layout, the helper
   functions and the review checklist change. The two scripts here follow the
   `navidrome` pair as it stood on 2026-10-05.
3. Ask for the category and the logo when filling the website record.
