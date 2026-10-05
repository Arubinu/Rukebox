# Rukebox - reference guide

Everything about installing, configuring and running Rukebox, in detail.
For a first look, start with the [README](../README.md).

## Project structure

```
src/rukebox_daemon.py       The radio daemon: playback, scheduler, control socket
src/web_server.py           The web interface's API (Flask) and static files
src/mpv_controller.py       mpv driven through its IPC socket, one file at a time
src/state.py                Persistent state: play queue, requests, daily triggers
src/playlist.py             Play orders (random, random by album, ordered)
src/library.py              Music library catalogue (tags, search, suggestions)
src/music_lists.py          The music lists: manual, or by genre
src/schedules.py            Schedules: other hours, other settings, chosen days
src/likes.py                Liked tracks
src/duplicates.py           Duplicate tracks, from the catalogue
src/hidden_tracks.py        Copies set aside by the duplicate check
src/json_file.py            Locked, atomic writes of the JSON stores
src/track_media.py          Cover art, tags and lyrics of the playing track
src/announcements.py        User-defined announcement types
src/track_order.py          Saved order of announcement folders
src/config_schema.py        Every setting, with its default and documentation
src/config_file.py          Reads/writes rukebox.yaml, generates rukebox.env
src/config_bundle.py        Configuration export / import
src/paths.py                The four roots (/config, /data, /music) and how they move
src/platform.py             What the machine is and what it can do
src/system_actions.py       Services, power and the clock: one call per platform
src/stream.py               The network output: encoded and served over HTTP
src/stats.py                Usage statistics (SQLite)
src/suggestions.py          Suggestion box, devices, nicknames and votes
src/audio_output.py         Audio outputs (Bluetooth, jack, USB, HDMI)
src/audio_diag.py           The audio diagnostic, and when the Flic button is put on hold
src/net_diag.py             The network diagnostic
src/bt_link.py              Which Bluetooth controller carries the speaker
src/bt_codec.py             The Bluetooth codecs offered to the speaker
src/control_client.py       The control socket's client (web server, buttons)
src/bt_clock.py             Clock fallback over Bluetooth
src/captive_portal.py       Captive portal of the access point
src/flic_click.py           Flic button bridge
src/gpio_click.py           GPIO button bridge
src/speaker_buttons.py      The Bluetooth speaker's own buttons
src/gpio_reset.py           Password reset by grounding a GPIO pin at boot
src/gpio_pins.py            The 40-pin header and which pins may be used
src/web_auth.py             The optional web password (PBKDF2)
src/version.py              Installed version (tree hash, git or release)
src/install_status.py       Progress page of the first-boot installation
web/                        The web interface (HTML, CSS, JavaScript, six languages)
scripts/install.sh          Installer (run on the Pi)
scripts/update.sh           Updater: USB, Git or GitHub release, with rollback
scripts/firstboot.sh        Automatic installation on the first boot
scripts/*.sh                Access point, USB gadget, Bluetooth, LED, Wi-Fi helpers
systemd/*.service           One unit per component
docker/                     The container: supervisor, compose files, PipeWire sink
Dockerfile                  The image (ghcr.io/arubinu/rukebox)
config/                     Configuration template and sudo grants
bootstrap/                  Card preparation and pushes from a computer
tests/                      Unit tests (python3 -m unittest discover -s tests)
```

## Installing on a blank SD card (macOS / Linux / Windows)

> **The simple way: `rukebox-setup.html`.** Flash Raspberry Pi OS Lite
> with Raspberry Pi Imager (if you fill in its settings, the user name must
> be `pi`), put the card back into the computer and open
> `rukebox-setup.html` (attached to each release, or built with
> `python3 bootstrap/build_setup_page.py` into `dist/`) in a browser. In
> **Chrome or Edge** it writes the card itself; **Firefox and Safari** cannot
> write into a folder, so there it reads the card and prepares
> `rukebox-card.zip` instead, whose contents you copy to the card, replacing
> the files already there (`config.txt`, `cmdline.txt`, `firstrun.sh`). It asks for everything (account, Internet for the installation -
> Imager's Wi-Fi, another one, Ethernet or the USB cable - the Rukebox's own
> Wi-Fi, passwords, a few radio settings, optionally some files) and writes
> the card. Then the Pi installs itself on its first boot: access point first, then the Internet (the Wi-Fi given when
> flashing, kept, another one, an Ethernet cable, or the computer's connection over the USB
> cable), the software, your files, then it restarts into the radio. A
> progress page shows each step meanwhile, over the USB cable and on the
> access point, and the installation resumes where it stopped if the Pi is
> unplugged. A password and/or an SSH key given in its Account step are
> always applied, even when the account itself is left to Raspberry Pi
> Imager (the two coexist: the key is added, Imager's password is kept
> unless you give one). The steps below remain the manual way.

Before the regular installation below, you need a Pi reachable via SSH.
Since this project assumes there will **never be any Wi-Fi**, the most
practical starting point is the Pi Zero's **USB gadget mode**: plugged
into a computer via USB, the Pi shows up as a network interface and
becomes reachable via SSH — no wireless network, screen or keyboard
needed. Works the same way on macOS, Linux and Windows.

### 1. Flash the OS

Flash **Raspberry Pi OS Lite** (32 or 64-bit depending on your model) to
the SD card with [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
or balenaEtcher — in simple mode, without using the Imager's advanced
options (the script below handles that instead, in a reproducible,
scripted way).

### 2. Provision the card

Once flashing is done, the computer automatically mounts the SD card's
boot partition (FAT32) — that's the one to target, not the whole card.

**macOS / Linux:**
```bash
cd bootstrap
./provision_sdcard.sh
```
The script scans common mount points (`/Volumes/*` on macOS,
`/media/$USER/*` on Linux) for a boot partition and offers it as a
suggestion to confirm, so you usually don't need to type the path
yourself. If nothing is detected, typical paths are `/Volumes/bootfs`
on macOS and `/media/$USER/bootfs` on Linux (or use `lsblk` to find the
mount point).

**Windows (PowerShell):**
```powershell
cd bootstrap
.\provision_sdcard.cmd
```

Windows blocks unsigned PowerShell script execution by default
(`Restricted` policy), regardless of content — `provision_sdcard.cmd`
works around that automatically for this one run (`-ExecutionPolicy
Bypass`), without changing anything permanent on your machine.
Double-click it directly in File Explorer if you prefer. If you run
`provision_sdcard.ps1` directly and PowerShell refuses with a "not
digitally signed" error, that's exactly this restriction: run it via
the `.cmd` instead, or one-off:
```powershell
powershell -ExecutionPolicy Bypass -File .\provision_sdcard.ps1
```
The script also scans all filesystem drives for `config.txt` and offers
the detected boot partition as a suggestion to confirm.

The script asks for a password for the `pi` user, optionally the path
to an SSH public key (recommended — passwordless login afterward), and
the desired hostname (`rukebox` by default). It then writes to the card:
- an `ssh` file (enables SSH from the first boot)
- a `userconf.txt` file (Raspberry Pi OS's official first-boot user
  creation mechanism — required since recent images ship with no
  default user at all; the password is stored as a proper SHA-512
  hash, computed via `openssl passwd -6`, or via WSL's `openssl` if
  not found directly on Windows)
- `dtoverlay=dwc2,dr_mode=otg` in `config.txt` + `modules-load=dwc2` in
  `cmdline.txt` (enables USB gadget mode; the gadget itself is a CDC-NCM
  one built at each boot by `scripts/usb_gadget.sh`, which is what lets
  Windows bind it with no driver to install by hand)
- `rukebox-account.env`, holding the password and the SSH key: `firstrun.sh`
  moves it to `/etc/rukebox/account-setup.env` and deletes it from the card
  before doing anything else, so no secret stays on the boot partition. The
  key Imager was given is read from its own files on the card (`user-data`,
  or its `firstrun.sh`) and written there, so the card installs it itself —
  on these images the account cloud-init builds from Imager's settings never
  gets its key
- a generated `firstrun.sh` (Raspberry Pi OS's official first-boot
  mechanism, the same one Raspberry Pi Imager uses internally) that will
  enable SSH, set the hostname, configure a fixed IP address on the USB
  interface (see step 3), and hand the credentials to
  `rukebox-account.service`
- a full copy of this project, next to `firstrun.sh`

Base64 is not encryption: while the card is out of the Pi, `rukebox-account.env`
is readable by anyone, as are Imager's own `user-data`/`meta-data` (the Wi-Fi
password in clear). The first is deleted at the first boot, the other two by the
installation itself.

The account itself is finished by `scripts/account_setup.sh`, installed
as `/usr/local/sbin/rukebox-account-setup`. The very first boot stops at
`kernel-command-line.target`, before the system services that create the
account, so the same script runs again from `rukebox-account.service` on
the second boot, once cloud-init or Raspberry Pi OS's own first-boot
wizard has done its part — that also means the credentials you gave are
applied *after* whatever Imager set, not before. That script is what
gives `pi` a login shell (recent images ship their first user with
`/usr/sbin/nologin`), sets the password or locks it when only a key was
given, appends the key to `~/.ssh/authorized_keys`, copies the project
into `/home/pi/rukebox` and applies the configuration bundle. It runs at
every boot until it has succeeded, and writes what it did to
`rukebox-account.log` on the boot partition, readable from the computer
with a card reader.

### 3. First boot and connection

Safely eject the card, insert it into the Pi, power it on. The Pi boots,
runs the provisioning script automatically, then **reboots itself** once
done — you don't need to do anything, just wait about one to two
minutes. Then connect the Pi to the computer with a USB cable — **on
the "DATA" USB port**, not "PWR", on a Pi Zero/Zero 2 W (the two
micro-USB ports are not interchangeable).

```bash
ssh pi@169.254.7.7
```

This is a **fixed link-local address** (RFC 3927) configured on the
Pi's USB interface during provisioning. Every mainstream OS (Windows,
macOS, Linux) auto-assigns itself a compatible `169.254.x.x` address on
a DHCP-less Ethernet-like interface, natively, with no extra software —
this is standard OS-level "link-local" behavior (Windows calls it
APIPA), not an mDNS/Bonjour-style service. So there's nothing to
install and nothing to discover: the address is always the same.

`ssh pi@rukebox.local` also works as a fallback wherever mDNS is
already available (native on macOS/Linux; on Windows only if Bonjour
happens to be installed for another reason) — but it's no longer
required.

> **If you see "This account is currently not available"**: the account
> still has `/usr/sbin/nologin` as its shell. `rukebox-account.log` on the
> card's boot partition says what happened, and the account setup runs
> again at every boot until it succeeds, so a reboot is usually enough —
> if the log keeps saying so, re-run the provisioning on a freshly
> re-flashed card.

> **If SSH asks for a password while you gave a key**: the key is not in
> `~/.ssh/authorized_keys` yet. `ssh -v pi@rukebox.local` says which key
> it offers, and `rukebox-account.log` (boot partition, readable with a
> card reader) says whether the account setup ran. Check that the key
> pasted when provisioning was the *public* one (`id_ed25519.pub`), and
> not the private file.
>
> **If it keeps asking and you cannot get in at all**, the card is the way
> back: power the Pi off, put the card in the computer and run
> `bootstrap\rescue_ssh.cmd` (or `rescue_ssh.ps1 -Path E:\`). It writes a
> one-shot `firstrun.sh` that, at the next boot, installs your key, puts
> `/bin/bash` back as the login shell, and records what the account looked
> like — permissions, the account service state, cloud-init, the two logs
> — in `rescue.log` on the card. Then forget the old host keys (a fresh
> card regenerates them) and log in:
> `ssh-keygen -R rukebox.local`, `ssh-keygen -R 169.254.7.7`,
> `ssh-keygen -R <the Pi's address>`, `ssh pi@169.254.7.7`.

> **If your router shows the Pi under a different name** (or alternates
> between two): the name it keeps is the one the Pi announced when it took
> its DHCP lease. At the second boot, Raspberry Pi Imager's own settings
> apply its hostname before this project's is put back, and that first name
> stays in the router until the lease is renewed — a router reboot, or
> removing the device from its list, clears it. New cards are protected
> against it: `firstrun.sh` writes a cloud-init drop-in
> (`preserve_hostname: true`), so Imager's hostname is never applied.

> **If `169.254.7.7` doesn't answer**, work out which end is missing
> before changing anything:
>
> - **On the Pi** (over the access point, or a screen),
>   `systemctl status rukebox-usb-gadget` should say it bound the gadget,
>   `ls /sys/class/udc/` should name a controller, and
>   `nmcli device status` should show `usb0` as *connected*. An empty
>   `/sys/class/udc` means the overlay isn't active:
>   `/boot/firmware/config.txt` needs `dtoverlay=dwc2,dr_mode=otg` under
>   `[all]`, then a reboot. Beware that a stock config.txt already ships
>   a `dtoverlay=dwc2,dr_mode=host` line — but under `[cm5]`, so it does
>   nothing on a Zero 2 W while still looking, to a careless grep, like
>   the setting is already there. If `usb0` exists but shows as
>   *unmanaged*, the udev override is missing — see
>   `scripts/usb_gadget.sh`.
> - **On the computer**, a new network interface has to appear when the
>   cable goes in. Windows names it "UsbNcm Host Device" and needs no
>   driver installed; macOS and Linux pick it up natively. If nothing
>   appears **at all**, it's the cable — a charge-only cable, a
>   charge-only hub port, or the "PWR" micro-USB port instead of "DATA".
>
> Until the USB link works, `ssh pi@rukebox.local` over the admin access
> point (or your own network) is the way in.

### 4. Finish the installation (Internet access needed once)

At this point, the project files are present (`~/rukebox`) but
nothing is installed yet: `apt-get`/`pip` need Internet access, which
doesn't exist yet on this Pi, designed to never have permanent access.
Two ways to get **temporary** access, just for this one step:

- **Internet sharing over the already-connected USB cable** (the
  simplest, no second cable or nearby Wi-Fi network needed):
  - macOS: `bash bootstrap/usb_internet_macos.sh` (see "Internet over
    the USB cable" below). Not macOS's own Internet Sharing: it serves
    its own 192.168.2.x network by DHCP, and the Pi's cards have fixed
    addresses.
  - Windows: double-click `bootstrap\usb_internet.cmd` (see "Internet
    over the USB cable" below). No "Sharing" checkbox: that one
    re-addresses the Pi's card and cuts you off from `169.254.7.7`.
  - Linux: `bash bootstrap/usb_internet_linux.sh`
- **Or** temporarily connect to a nearby Wi-Fi access point (a phone's
  hotspot, for example) directly from the Pi:
  `sudo nmcli device wifi connect "<SSID>" password "<password>"`

Once one of those is set up:

```bash
sudo ~/rukebox/scripts/install.sh
```

`install.sh` checks for Internet access itself (a direct TCP connection
test, not `ping` — more reliable over a phone hotspot or shared
connection) and stops with a clear message if it can't reach anything,
rather than failing confusingly partway through `apt-get`. While it has
that access, it also **offers to upgrade the rest of the system
packages** (`apt-get upgrade`, a few minutes on a Zero 2 W, declined by
default) — this is the one moment where the Pi is online, plugged in and
running nothing yet, which is exactly when a NetworkManager restart
costs nothing. `RUKEBOX_SYSTEM_UPGRADE=yes` answers it without a prompt.
Near the end, it asks for the admin access point's name (SSID) and an optional
password, then an optional web interface password (same as the AP's, or
a different one, your choice) — and the access point is already up by
the time the script finishes, no separate step needed. See "Admin
access point" below for details, and for changing either password
later from the web interface itself.

**Or, from the computer holding this project, skip the `ssh` step
entirely:**

```bash
./bootstrap/push_install.sh          (macOS/Linux)
bootstrap\push_install.cmd           (Windows)
```

Same USB link, no manual SSH session needed — it packs the project,
sends it over, and runs `install.sh` on the Pi for you (prompting for
the account's password if that's what it needs). Also accepts `--host`/
`--user` to target a Pi over your own network instead of the USB cable
— run `push_install.sh --help` for the full list. If the default USB
address doesn't answer and no `--host` was given, it automatically
retries once against `rukebox.local` before giving up, so it also works
unplugged, over your own Wi-Fi, once mDNS has had a chance to come up.

The admin access point is already active by the time this finishes —
connect your phone to it and continue from the web interface (speaker
MAC address, times, audio folders, cutoff mode — or edit
`/etc/rukebox/rukebox.yaml` directly, see "Configuration" below). Then
continue with the remaining optional steps (Flic, GPIO button,
clock...) — from there on, no more Internet needed day to day.

## Installation (on a Pi already reachable via SSH)

On the Raspberry Pi (OS Lite recommended, no desktop environment for a
faster boot):

```bash
git clone <this folder or an archive> rukebox
cd rukebox
sudo ./scripts/install.sh
```

The script installs `mpv`, sets up the folders, copies the files to
`/opt/rukebox`, creates `/etc/rukebox/rukebox.yaml`, enables the systemd
services so they start automatically at boot, and — interactively, or
from `RUKEBOX_AP_SSID`/`RUKEBOX_AP_PASSWORD`/`RUKEBOX_WEB_PASSWORD` for a
scripted install — sets up the admin access point and an optional web
interface password. See "Admin access point" below. It also offers a set of
**optional boot tweaks** (asked once, default yes; `RUKEBOX_BOOT_TWEAKS=yes|no`
for scripted installs): boot to console instead of the desktop, don't
wait for network connectivity at boot, disable `cloud-init`, and use
minimum GPU memory with no boot splash. They change the OS rather than
Rukebox, so nothing is applied without saying yes, and the undo
commands are printed if you decline. Measured on a Pi Zero 2 W:
**38.6s down to 33.3s** of boot time, almost all of it from `cloud-init`.

If your image manages Wi-Fi through **netplan**, moving that connection
to a native NetworkManager profile is worth another ~9s: netplan makes
NetworkManager run `systemctl daemon-reload` several times during boot
(~3.2s each on a Pi Zero 2 W). `nmcli connection clone <netplan-conn>
<new-name>` copies it, password included, into
`/etc/NetworkManager/system-connections/`, after which
`/etc/netplan/*.yaml` can go. Measured end result: **24.2s**. Pin the
result with `nmcli connection modify <new-name>
connection.interface-name wlan0` — an unpinned Wi-Fi profile can come
up on the access point's interface instead and take the hotspot down.

**Then set the radio up** from the web interface (speaker, times, audio
folders, cutoff), or by editing `/etc/rukebox/rukebox.yaml` over SSH. What
the interface saves applies at once; a hand edit is read when the services
restart (`sudo systemctl restart rukebox-daemon.service`). See
"Configuration" below for how this file works and how it relates to the web
interface's settings form.

## Running in a container (Docker)

Rukebox runs on anything with Docker — a NAS, a mini-PC, a server — with a
different set of features, because a container has no access point, no GPIO
pin, no hardware clock and no USB gadget. The interface hides the cards that
would lead nowhere (see "What a machine can do" below); what is left is the
radio, its music, its schedules, its statistics and its web interface.

The image is `ghcr.io/arubinu/rukebox`, published for `amd64`, `arm64` and
`armv7` — a Pi Zero 2 W runs the armv7 one. Four `docker compose` files in
`docker/` are the four ways of hearing it:

```bash
# 1. The network stream, and nothing else. Always works.
docker compose -f docker/compose.stream.yml up -d

# 2. A sound card of THIS machine, given to the container
docker compose -f docker/compose.alsa.yml up -d

# 3. The PipeWire already running on this machine
docker compose -f docker/compose.pipewire.yml up -d

# 4. Bluetooth, through the host's BlueZ (network_mode: host)
docker compose -f docker/compose.bluetooth.yml up -d
```

Then open `http://<this machine>:8080` (port 80 in variant 4, which shares
the host's network). The three volumes are the three roots the project uses
everywhere, moved by the environment rather than by a rewritten file:

| In the container | What it holds | On a Pi |
|---|---|---|
| `/config` | `rukebox.yaml`, the JSON files it manages | `/etc/rukebox` |
| `/data` | statistics, library catalogue, queue, likes | `/var/lib/rukebox` |
| `/music` | the audio tree, mounted read-only if you like | `/home/pi/audio` |

`RUKEBOX_CONFIG_DIR`, `RUKEBOX_STATE_DIR`, `RUKEBOX_MUSIC_DIR` and
`RUKEBOX_INSTALL_DIR` move them; inside the container they are set by the
image. The configuration is written on the first start, with those paths in
it — there is nothing to prepare and nothing to edit before the first `up`.

### Variant 1: hearing it at all

A container with no sound card still plays: it plays into a **virtual
output** of its own (`docker/pipewire-container.conf`, a PipeWire null sink
named `rukebox_output`), and the network stream encodes that output's
monitor. That is what "Listen here" on the player plays, and what VLC or a
network speaker plays from `http://<host>:8080/stream.opus`.

**It is a real output, in the list like any other.** The first start writes
`AUDIO_OUTPUT=docker` into the configuration, which the **Audio output** card
shows as *Network stream* — so the card says where the sound goes, the daemon
points mpv at that sink, and the card's own line explains that it is heard
rather than played. Choosing another output there works exactly as on a Pi;
the file is the single source of truth from then on (not an environment
variable, which would win over the file and undo the choice at the next
restart).

Turn the stream on in the same card (**Network audio stream** — it is on by
default in the image, because on a container it *is* the output).
`STREAM_ENCODER` picks the codec (`opus` by default, which every browser
plays).

**Think about the password first**: anyone who can reach the page can listen
to the library. `WEB_PASSWORD_HASH` is the only thing in the way, and on a
container the network around it is usually not a private access point.

### What a machine can do

`src/platform.py` decides, once, what the machine is (`pi`, `lxc`, `docker`,
`host`) and what it can do — an access point, Bluetooth, GPIO, a hardware
clock, a local sound card, switching itself off, updating itself in place.
`/api/status` publishes that list, the interface hides what is missing, and
the routes behind those cards answer `unsupported_here` rather than failing
on a binary that is not installed.

`RUKEBOX_PLATFORM` forces the answer, which is how the test suite runs as a
Pi by default and as a container in `tests/web/platform.test.js` and
`tests/test_platform.py`.

Two consequences worth knowing before you look for a card:

- **"Switch off" ends the container's processes**, and `restart:
  unless-stopped` in the compose file is what brings them back — so "Restart
  the service" and the evening cutoff both work, without a systemd.
- **"Update"** cannot replace the tree in place: the image is the unit of
  update. The card shows the one line to run (`docker compose pull`), and
  `UPDATE_DOCKER_IMAGE` is the image it names.

## Running in an LXC container (Proxmox, or any host with lxc)

The same installer that runs on a Pi takes a **container profile**, which
keeps systemd, the units, `update.sh`, the daemon, the web interface, the
schedules and the statistics, and leaves out what a container cannot have.
It is chosen by itself — `/dev/lxc` or `container=lxc` — and can be forced:

```bash
git clone https://github.com/Arubinu/Rukebox.git && cd Rukebox
sudo RUKEBOX_PROFILE=lxc ./scripts/install.sh
```

What changes, compared with a Pi:

| | Pi | Container |
|---|---|---|
| Services run as | `pi` | `rukebox` (created by the installer) |
| Audio folders | `/home/pi/audio` | `/srv/rukebox/audio` |
| Access point, Wi-Fi country | installed | skipped |
| USB gadget, GPIO, activity LED | installed | skipped |
| Hardware clock (RTC), boot tweaks, `config.txt` | installed | skipped |
| Flic button | available | disabled (no controller of its own) |
| Daemon, web interface, schedules, statistics, updater | yes | yes |

`RUKEBOX_USER` and `RUKEBOX_AUDIO_ROOT` override the account and the audio
folder; the installer moves the paths in the generated YAML to match.

**Sound** is the part to think about before starting: a container has no card
of its own. Either pass a USB sound card through to it, or let it play into a
virtual output and listen over the network — the same choice the Docker
section above describes, and `docker/pipewire-container.conf` is the
PipeWire drop-in that does it.

**Bluetooth** works through the host's BlueZ, from inside the container, if
the host's D-Bus socket is passed to it. Without that, pair the speaker on
the host.

### The Proxmox helper scripts

`community-scripts/` holds what a
[community-scripts](https://github.com/community-scripts/ProxmoxVE) proposal
needs — `ct/rukebox.sh`, `install/rukebox-install.sh` and a draft of the
metadata — written to their rules and hosted here for now:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Arubinu/Rukebox/main/community-scripts/ct/rukebox.sh)"
```

On a Proxmox host, as root. The `ct/` script creates the container (2 cores,
1 GB, 4 GB, Debian 13) and the install script runs this project's own
installer with `RUKEBOX_PROFILE=lxc` inside it.

Read `community-scripts/README.md` before proposing them: a **new** script
goes to their DevScripts repository, never straight to ProxmoxVE, the
metadata is managed on their website rather than in a file, and neither of
those two scripts has been run on a real Proxmox host yet.

## Admin access point

Created automatically by `install.sh` (SSID and an optional password,
asked interactively or via environment variables — see above), on the
same virtual interface (`uap0`) and concurrent AP+STA setup described
under "Networking" below. An **empty password means an open network**
— anyone in range can then connect and reach the admin interface with
no authentication of their own; fine for some setups, but know what
you're choosing. The password must be **at least 8 characters**
otherwise (WPA2's own minimum).

**Change either value later from the web interface's own Access Point
card** (Network tab) — applied immediately, no service restart needed.
Leaving the password field empty and saving **keeps the current
password** (handy for just renaming the network); tick **"Open
network"** to remove it instead. Changing either briefly disconnects
anyone currently connected, the same as changing the Wi-Fi password on
an ordinary router.

Equivalent from the command line, if you'd rather not use the web
interface (same script `install.sh` already calls):

```bash
sudo /opt/rukebox/scripts/setup_ap.sh "My Network" "a-strong-password"
sudo /opt/rukebox/scripts/setup_ap.sh "My Network"                     # open, no password
```

`install.sh` also asks for a **2-letter Wi-Fi country code** (`FR`,
`US`, `DE`...), used purely for regulatory compliance — which channels
and transmit power the radio may legally use where you are. It's
skippable (blank), and settable later with `sudo raspi-config nonint
do_wifi_country XX`.

> **Note on `nmcli device wifi hotspot`** — if you're used to setting a
> Pi hotspot up with that one-liner and it hangs for ~25 seconds then
> fails with `802.1X supplicant took too long to authenticate`, that's
> a known Debian Trixie + `brcmfmac` bug
> ([raspberrypi/linux#7247](https://github.com/raspberrypi/linux/issues/7247)),
> not something you did wrong — and it happens even for an open network
> with no password at all. This project sidesteps it by building the
> connection profile and activating it as two separate `nmcli` steps,
> which works in about two seconds. Also harmless, and unrelated:
> `iw reg get` showing `phy#0 country 99: DFS-UNSET` while the global
> domain is correct is normal for this chip; the access point works
> fine that way.

## Flic button

Everything is done from **Audio > Bluetooth > Controllers and Flic button**
(detailed view):

1. **Install the Flic software** (the official `fliclib-linux-hci` SDK;
   needs Internet once). `install.sh` also downloads it when the Pi is
   online.
2. **Choose the controllers**: the speaker on one, the Flic button on the
   other, then Save. `flicd` takes its controller for itself (HCI user
   channel), so with a single controller it is either the Flic button or
   the Bluetooth speaker - a USB dongle gives you both (see "Using the USB
   port for devices instead").
3. **Use the Flic button**: starts `flicd` and the bridge, now and at every
   boot.
4. **Pair a button**: hold it for 7 seconds, away from any phone it is
   paired with. Paired buttons are listed there, each with Forget.

Controllers are stored by **address** (`adapter` and `flic_hci_device`
under `bluetooth:`), because their `hciN` names can swap between boots.
`scripts/start_flicd.sh` turns the address into the current `hciN`, picks
the `flicd` binary for the processor (`aarch64` on a 64-bit system,
`armv6l` on 32-bit) and runs `flicd -f <db> -h <hciN> -w`. To check a
run: `journalctl -u flicd.service -n 30`.

**Which controller carries the speaker is checked on every controller, not
just the one chosen.** BlueZ keeps a device per controller: a speaker paired
on the built-in and one on the dongle are two different devices, and asking
the wrong one answers "not available" - which looks exactly like a speaker
that is switched off. The page therefore says **which** controller the
speaker is on, and warns when it is not the one the settings name (the sound
follows the controller the speaker is on). When it can, the daemon also asks
the chosen controller to take the speaker back - a controller that has never
seen it cannot, and then the speaker has to be paired there once.

One thing to know when both are in use: **`flicd` takes its controller out
of BlueZ** (HCI user channel), exactly as the paragraph above says. A speaker
paired on that controller can then no longer connect at all, so once the Flic
button is in use the speaker *must* live on the other controller.

### The Flic button is put on hold by itself when it cannot work

With **one** controller and the sound on Bluetooth, the Flic button and the
speaker cannot both work: the radio the speaker streams on is the one `flicd`
would take for itself. The daemon then **stops `flicd` and the bridge on its
own** (within ten seconds, and within three at boot), rather than let the
music lose its radio.

This is a hold, not a decision:

- the switch stays where you left it - **on** - and the page dims it and says
  why. Nothing is written down as "off";
- the moment a second controller is there (a USB dongle plugged in) or the
  sound goes to a wired output, the daemon **starts the two services again**
  by itself, and the button works as before;
- turning the switch off while it is on hold is the one thing left to do from
  there, and it is final: the services are disabled, and nothing starts them
  again. Turning the switch back on is refused while there is only one
  controller - the page says what to plug in.

`journalctl -u rukebox-daemon | grep -i flic` shows both sides of it:
`Flic button on hold (single_controller): the speaker needs the only Bluetooth
controller there is`, then `Flic button back on: a controller is free again`.

### Working without a Flic button

The Flic button is entirely optional. If you don't have a second
Bluetooth interface available, if you'd rather not install one, or if
the button simply isn't in range some day: **the rest of the system
keeps working normally**, with no dependency on `flicd` or
`flic-bridge`:

- Music playback, ReplayGain, scheduled announcements (morning/cutoff),
  the clock, the web access point: none of that depends on the button.
- The button's three actions (sound + next, dedicated announcement,
  stop + shutdown) remain available at any time from the web interface
  — they're actually the exact same internal functions being called,
  button or not.
- Simply don't launch `flicd.service` and `flic-bridge.service` (or
  disable them: `sudo systemctl disable --now flicd.service
  flic-bridge.service`). The web interface's "Flic button" card will
  then show "Not used", purely for information — it doesn't affect
  anything else.

Once the button is paired:

```bash
sudo systemctl enable --now flicd.service
sudo systemctl enable --now flic-bridge.service
```

### The three gestures

| Gesture | Action |
|---|---|
| **Single click** | Quick fade, then one sound of the chosen list (the button sounds by default), then the next track. If music hasn't started yet (`MUSIC_START_MODE` is `action`, `scheduled` or `bluetooth`), the first single click always starts it instead, regardless of that setting. |
| **Double click** | Same, with its own list (the double-click announcement by default). Ignored while the Pi is still idle. |
| **Long press** | Fade (more gradual), full stop, then shuts down the Pi — whether music is playing or the Pi is still idle. Or, with `long_press_action: standby` (Buttons card), only **standby**: the music stops and the Pi stays on, waiting like at startup. |

**Every action that stops the music does so via a progressive fade by
default**, not an abrupt cut — whether it's the single click, double
click, long press, or scheduled events (morning announcement, cutoff in
`exact` mode). Three separate fade durations, adjustable independently:

```bash
INTERACTIVE_FADE_DURATION_SEC=1.5   # single click / double click: stays responsive
LONGPRESS_FADE_DURATION_SEC=4       # long press: more gradual stop before shutdown
PAUSE_FADE_SEC=1                    # Pause/Resume button: out, then back in (0 = at once, max 5)
FADE_DURATION_SEC=15                # scheduled announcements (morning / exact cutoff)
```

Set `INTERACTIVE_FADE_DURATION_SEC` to `0` for an instant cut instead
of a fade when skipping/switching tracks on a single or double click —
it's still the same volume-ramp code underneath, just with a
zero-length ramp, so no separate "hard cut" setting was needed.

These same actions are also available as buttons in the web interface
(see below), to control the radio without the physical button. Now
Playing also has **Previous**, a **loop** button (none → album → track,
for this session) and **Timer…**: pause for a while (`pause_durations`,
the music starts again by itself) or fall asleep after one of
`sleep_durations` minutes (a fade, then silence). **Previous** says what
it will do: once the song has played a few seconds
(`PREVIOUS_RESTART_AFTER_SEC`, 5) it reads *Back to the start* and does
exactly that, and earlier in the song it reads *Previous* and goes back
to the song before - the same rule the Flic button and a speaker button
follow on the daemon's side. Under the track it
shows the next one, and below the buttons
what is worth knowing right now: when the music will start, or what
stops it (speaker not connected, output unplugged, time not set, empty
library...).

**Start with a fade-in** (`start_fade_sec`, next to the start mode):
the first song rises from silence over that many seconds - gentle for a
wake-up.

The **System** tab shows the machine's health (uptime, temperature,
memory, storage, power supply, addresses) and the state of each Rukebox
service, with a **Restart** for those in use. On a fresh installation,
**To finish** at the top of Home lists what is still missing (speaker,
time source, timezone, music, passwords) with a way there; anything can
be set aside.

**Guests** (guest mode on, password set) spend credits: each action has a
price (Network > Guest access), doubled every time the same guest
repeats it within a few minutes, and credits come back over time. Anyone
can put a song of **Recently played** or the **Library** up next, then
start one of **Up next** at once (two prices, which add up), and when the planned audio
output is gone (speaker off, card unplugged) anyone may send the sound to
another one until it comes back. **Locked for guests** (Network > Guest
access) takes commands away from guests altogether, whatever their credits
and even for a device spared them: next, previous, start, pause, sound,
announcement, volume, up next, play now, another output. Their buttons stay
on the guests' page, greyed with a padlock, and the Pi refuses them anyway
(`guest_locked`). **When several people press at once**, what one of them just did is not
undone or doubled by the others: for a moment after an action ends (a fade
included), the same kind of action from someone else is not run - they get
the first answer and pay nothing. That moment is one second by default
(**Network > Guest access > Same action from someone else ignored for**,
`action_repeat_sec`, 0 turns it off), and it applies to everyone, the owner
included. The kinds are: changing the song (next,
previous, back to the start, a button sound, play now), play/pause and
standby, the volume, mute, and loop. The person who just acted may go on (a
second Previous goes one song further back, a volume slider keeps moving),
except for a double tap on a toggle such as Pause, which counts once. Two
people queueing two different songs both get their song. **Connected devices** (Network) lists who
is on the Rukebox now, with their nickname (a generated one until they
choose). It is not only the access point's own clients: a device that
reaches the interface over the owner's home network never joins the
access point, and the Pi cannot see the clients of somebody else's router
either, so a request within the last two minutes counts as being there
too - those rows say **personal network** and how long ago they were seen,
and they cannot be disconnected, since they are on nobody's radio of ours.
Each row opens its own controls with the chevron at its right: **Name**
names the device and, once **Name locked** is on, stops it from changing
that name itself (a small padlock then shows beside it in the list);
**No credits** spares that device the guest credits (every action is free
for it, the other guests keep paying), and **Captive portal** decides
whether the phone's captive portal keeps holding it - **General setting**
follows the rule of the Guest access page, **Always** brings the
page back at every connection (the way to see it again, or to put it in
front of someone), **Never** lets the device through without ever being
asked. **Show the portal again** is the other half of that page: a device
that tapped "Finish connecting" is let in until the tap is forgotten, and
this button forgets it, so the portal holds the device again at its next
connection. The tap is remembered against the device, not against the
address it happened to have.

**Same person** links a device to another one, for someone who uses a phone
and a computer: the linked device takes the other's name, votes, suggestions,
credits and ban, and from then on a rename, a vote, a credit spent or a ban on
either one counts for both (one vote per suggestion, one credit counter). The
captive portal stays each device's own. **Link…** picks the other device among
the named ones; **Unlink** takes this device out, and it leaves with nothing,
like a device seen for the first time, while the others keep the name.
A linked device can also leave by itself (**my devices** > **Leave**): it
keeps the credits already spent together, so leaving is not a way to a full
counter.

People can do it themselves: on the Suggestions page, **my devices** shows a
six-digit code on one device (five minutes, one use) to type on the other
one. The device that types the code takes the name of the one that shows it.
Five wrong codes lock the form for five minutes, and only the owner unlinks.

Two more pages go with it. **Previously connected** is everyone the
Rukebox has seen this week and which is not on it right now, most recent
first, with **Show more**; the dates are precise to the second at first and
then loosen ("3 h ago", "yesterday at 21:14"), the way a site dates what it
saw. The same controls are there, which is the point: a device that has
just gone can still be named, spared the credits, or sent back to the
portal before it returns. It is also the page that lets a device go: a
**Delete** button next to **Ban** forgets that one device outright, and
**Delete the unnamed ones** sweeps away everyone who never took a name (a
phone that only checked for the portal, a passer-by). Forgetting is the
deepest thing here - the Rukebox loses the device's name, its credits, its
portal choice and its ban, so it is greeted as a complete stranger if it
comes back - which is why it always asks first. **Banned** keeps the bans
on their own page, with **Lift** to undo one and the date it was last seen
- a banned device appears in neither of the other two lists, and a banned
device that opens the interface anyway is told it is not allowed here. All
three pages have the same search box (name, address or MAC), which filters
what is on the page as you type and says how many it found.

**Share access** (Network) shows two QR codes - one to join the Wi-Fi, one
to open this page - with the network name, password and address written
under them, ready to print and leave next to the radio (handy for a PC
where the welcome page never appears). **Add to home screen** explains how
to pin the interface on a phone: the page is served over plain HTTP, so
the browser's own install prompt is not available there.

### Choosing what single and double click do

Both are configurable independently, from the web interface's
**Button actions** card (`rukebox.yaml`, under `buttons:`):

| Setting | Choices |
| --- | --- |
| Action (`single_click_action` / `double_click_action`) | `next` — next track · `previous` — previous track (the same one from the top once it has played 5 s) · `sound` — plays the sound below, then the same song goes on where it was · `playpause` / `pause` / `play` · `loop_track` / `loop_album` — loops the song / its album (press again: normal playback) · `loop_off` — normal playback · `volume_up` / `volume_down` — by `volume_step` · `sleep` — the music pauses in the shortest of `sleep_durations` minutes (press again: cancelled) · `off` — nothing |
| Sound played first (`single_click_source` / `double_click_source`) | Only with `next`, `previous` and `sound`: `none`, the button sounds (`meme`), the cutoff announcement (`cutoff`), or any custom announcement (`custom:<id>`, the morning and double-click ones included) |

The speaker's own buttons (`speaker_*_action`) take the same actions. A
double click set to `play` or `playpause` also starts the music from idle.

**One sound per click, in the announcement order** (`ANNOUNCE_ORDER_MODE`,
or the order saved in the Announcement files card): in a fixed order the
clicks play the list's 1st, 2nd, 3rd sound... then start over; shuffled,
every sound plays once before any comes back. Where the list stands is
kept across restarts. Choosing the **cutoff** list is safe: it only plays
a sound, it never starts the shutdown.

The **▶** beside each action tries it at once with the values shown -
saved or not - while a song is playing. Changes apply as soon as they are
saved.

**The speaker's own buttons** ("Speaker buttons" section of the same
card): play/pause, next and previous, as the speaker sends them over
Bluetooth (AVRCP), each with the same two choices and its own sound
(`speaker_playpause_action`, `speaker_next_action`,
`speaker_previous_action` and their `_source`). Which buttons reach the
Pi depends on the speaker - many keep some for themselves. From idle,
play/pause starts the music like a single click. Read by
`rukebox-speaker-buttons.service`, which waits quietly while no speaker
is connected.

## Cards and barcodes (RFID reader, barcode scanner)

A cheap USB RFID reader (125 kHz or 13.56 MHz, the kind sold as a "USB ID
reader") types the number of the card held on it, then Enter, like a
keyboard. `rukebox-card-reader.service` (root, since `/dev/input` is) finds it
by the name it announces - anything with "RFID", "ID&IC", "card reader" or
"Sycreader" in it, or the part of a name set in **Card reader**
(`hardware.rfid_reader`) - takes it for itself so the numbers never reach a
console, and hands each card to the daemon. The same card held for a while
counts once every 3 seconds. A USB **barcode scanner** works the same way (it is a keyboard
too, "barcode" or "scanner" in its name is enough): the code of a book, a CD or
any box then plays what you gave it - letters and digits, 4 to 32 of them.

**Settings > Cards & barcodes** (detailed view) lists the cards. Hold an unknown
card on the reader and the page says "Unknown card read: 0012345678" with
**Register it**; give it a name and what it does:

- **Plays a list** (or the whole library) - like choosing it in Lists;
- **Plays a folder** of the music library, in order from its first song;
  the radio goes on with the rest afterwards - a card per album, the
  Toniebox way;
- **Plays an announcement**;
- **Like a button**: any click action (next, pause, standby, say the time...).

The ▶ of a row does what the card would do, without the card. Cards live in
`/etc/rukebox/cards.json` (`paths.cards_file`), are read at every card (no
restart), and travel with the configuration export. The USB port of a Pi
Zero is the one the gadget uses: a reader needs `USB_PORT_MODE=host` (and a
hub if a Bluetooth dongle is plugged too). **Not tried with a real reader
yet.**

## GPIO button

Instead of, or in addition to, the Flic button, you can wire a plain
physical push-button straight to a GPIO pin. It's a second, completely
independent input: enable the Flic services, the GPIO service, or both
at once — pressing either sends the exact same single click / double
click / long press to the daemon, so everything above (the three
gestures, what single/double click do, the fades) applies identically
regardless of which button you pressed.

Wiring: one leg of the button to `GPIO_BUTTON_PIN` (BCM numbering,
default **GPIO20**, physical header pin 38), the other leg to any GND
pin. No resistor needed — the pin is configured with its own internal
pull-up, the same mechanism as the [GPIO password reset](#security)
pin below, just a different pin by default so both can be used on the
same Pi without conflict.

**You do not have to work the pin number out by hand.** Next to the pin
field in the web interface (Settings, and Security for the reset pin)
there is a **Choose…** button that draws the real 40-pin header:

- pins are shown in their physical positions, colour-coded by what they
  are (usable GPIO, power, ground), and tapping one fills the field;
- the pins this project cannot let you use are greyed out with the
  reason — I2C (the clock module), the serial console, the HAT
  identification pins — as is whichever pin the *other* feature already
  holds, so the button and the password reset can never be pointed at
  the same pin;
- **Detect by pressing** configures every usable pin with its pull-up
  and waits up to 20 seconds for one of them to be grounded: press your
  button and it fills in the pin for you. The button watcher service is
  paused for the duration, so the press being detected cannot also be
  acted on as a real click, and it is put back exactly as it was found.

The layout and the rules come from `src/gpio_pins.py`, served over
`/api/gpio/pinout` — the picker never carries its own copy, so the pins
it offers and the pins detection is willing to watch cannot drift apart.

Once wired:

```bash
sudo systemctl enable --now rukebox-gpio-button.service
```

Timing is adjustable from the **Flic and/or GPIO button** section of
`rukebox.yaml`, under `buttons:`:

```yaml
buttons:
  gpio_button_pin: 20                          # BCM pin number
  gpio_button_debounce_sec: 0.03                # mechanical bounce filter
  gpio_button_double_click_window_sec: 0.4      # max gap between two clicks
  gpio_button_long_press_sec: 1.5               # hold duration for a long press
```

Unlike the Flic button (whose firmware does this classification for
you), a plain GPIO button has no concept of "double click" on its own —
`rukebox-gpio-button.service` classifies the raw press/release signal
into the same three gestures itself, in software, using the timings
above.

## How it works

- **Music playback order, loop and resume**: music and announcements
  each have their own independent order setting
  (`MUSIC_ORDER_MODE` / `ANNOUNCE_ORDER_MODE`, `playback.order_mode` /
  `playback.announce_order_mode` in `rukebox.yaml`), one of:
  - `random` — completely random, the whole list reshuffled.
  - `random_albums` — random too, but round-robins across each
    subfolder under the music folder (used as an "artist" grouping) so
    the same artist never plays twice in a row, instead of the
    occasional run a plain shuffle can produce.
  - `ordered` — natural filename sort ("2" before "10" — prefix files
    "01 - ...", "02 - ..." to control it), or an explicit sequence you
    pick yourself for an **announcement** folder from the web
    interface's "Announcement files" card (not offered for the
    music library itself — impractical at the scale of a real music
    collection, natural sort/file naming is the only control there).

  **Rising start** (`MORNING_RISE_TRACKS`, `playback.rising_start_tracks`,
  0 = off, 30 at most): at each music start - the boot, a time, the
  speaker, a click - the next N songs of the queue are played from the
  quietest to the loudest, whatever the order mode, so the music wakes up
  with you. "Quiet" is the file's integrated loudness (EBU R128), measured
  once per file by the web server in the background, after the tags, with
  `ffmpeg` at the lowest priority: about 5 s a song on a Pi Zero 2 W, so a
  1 000-song library takes a couple of hours the first time; songs not
  measured yet keep their place after the measured ones. It is a measure of
  loudness, not of tempo: a quiet recording of a lively song still counts as
  quiet. Songs asked for stay first, and a start that takes the last song up
  again (`same_track` / `same_position`) is left alone.

  `MUSIC_LOOP` (`playback.loop`) controls what happens once the list
  has played through once: `true` (default) loops forever, like
  before. `false` stops after exactly one pass — the Pi and the
  Bluetooth speaker stay fully powered (so a scheduled announcement can
  still play normally), only the music itself stops; a single click, or
  "Start music" in the web interface, restarts the list (reshuffling it
  first, for the random modes).

  `MUSIC_KEEP_PROGRESS` (`playback.keep_progress`) controls what
  happens across the daily shutdown/restart: `true` (default) picks up
  where the list left off — necessary since the Pi shuts itself down
  and restarts every day (see below) — while `false` always starts a
  fresh pass (a full reshuffle, for the random modes). When
  `keep_progress` is on, `MUSIC_RESUME_MODE` (`playback.resume_mode`)
  decides what happens to the track that was interrupted mid-play by
  the shutdown: `next_track` (default) moves on as if it had finished,
  `same_track` replays it from the start, and `same_position` takes it up
  where it stopped, three seconds earlier - after a shutdown, a standby or
  a schedule's stop alike.

  The play queue itself is a single persisted list (`src/state.py`),
  built by `src/playlist.py` from whichever order mode is active.
- **ReplayGain**: mpv natively applies the `REPLAYGAIN_TRACK_GAIN` tags
  already present in your files (`REPLAYGAIN_MODE=track`), no
  re-encoding — light on the Pi Zero. If you ever need to re-tag,
  `scripts/tag_replaygain.sh` targets -14 LUFS with `rsgain`.
- **Morning announcement**: a custom announcement like any other (see
  "Adding your own announcement types"), created for a new radio at 05:58
  with the folder `/home/pi/audio/morning_announcements`. At its time:
  fade over `FADE_DURATION_SEC` seconds down to 0, pause, volume reset to
  `BASE_VOLUME`, plays ONE of its files (the next one in the
  announcement order, a different one each time), then resumes music. Rename it,
  change its time or delete it from the web interface. The **double-click
  announcement** is the same kind of entry,
  with no fixed time: it only plays on demand or from a button.
- **Cutoff at the configured time** (`CUTOFF_HOUR:CUTOFF_MINUTE`), two
  modes to choose from (`CUTOFF_MODE`):
  - `exact`: same behavior as the morning announcement (fade, one file
    of the cutoff folder), then shuts down instead of resuming music.
  - `end_of_track` (default): no fade, simply waits for the natural end
    of the current track, then one file of the cutoff folder, then
    shutdown. May run past the chosen time by the remaining duration of
    the track.

  The Pi waits a few seconds after the last file before cutting the
  speaker and powering off, so the end of the announcement is heard.
- **Flic button**: see the dedicated section above (single click,
  double click, long press).
- **Volume at startup**: whatever the volume was at the last shutdown,
  it's reset to `BASE_VOLUME` as soon as the daemon launches, before any
  playback.
- **Volume while listening**: the slider in the web interface applies
  immediately, and `VOLUME_MODE` (`playback.volume_mode`) decides what the
  next track change does with it — `session` (default) keeps the volume you set for as long as
  the daemon runs, `base` sends it back to `BASE_VOLUME` on every track
  (announcements and their fades still come back to the same value, not
  to 0). A restart always starts from `BASE_VOLUME`.
- **How a volume change is heard**: `VOLUME_CHANGE` (`playback.volume_change`)
  is `instant` (default) or `fade`, a glide to the new level over
  `VOLUME_FADE_SEC` seconds (1.5 by default) - in the Volume card's
  detailed view. A new change during a glide carries on from wherever it
  got to.
- **Shutdown**: disconnects Bluetooth from the speaker then
  `systemctl poweroff`. **After the cutoff** (Settings > Schedules > Every
  day, `SHUTDOWN_AFTER_CUTOFF`) chooses what the daily cutoff ends with:
  *Switch the Pi off*, or *Standby* - the Pi stays on, the speaker stays
  connected, and it waits exactly as it does at startup, so the next start
  (a time, the speaker, a click, a schedule) works. A long press does what
  its own setting says (switch off or standby), regardless of this one. The install script grants the
  `pi` user passwordless sudo to shut down (needed since the daemon runs
  without an interactive session).

## When the music starts

Four modes, adjustable via `MUSIC_START_MODE`:

- `boot` (default): music starts as soon as the daemon is up, the way a
  radio switched on at the wall behaves.
- `action`: **all initialization still happens normally** (mpv starts,
  the Bluetooth connection to the speaker happens, the clock
  synchronizes, the control socket and web server are ready), but no
  music plays. Playback only starts on the first single click on the
  button (or via "Start music" in the web interface).
- `scheduled`: silent exactly as in `action`, until
  `MUSIC_START_HOUR:MUSIC_START_MINUTE`, then playback starts on its
  own. A click before that time still starts it early — the schedule is
  a floor, not a lock — and once music is playing the schedule never
  interrupts it.
- `bluetooth`: silent exactly as in `action` until the Bluetooth speaker
  is connected, then playback starts on its own — the speaker becomes
  the on switch, which is useful when it is powered on separately from
  the Pi (or is simply not reachable yet when the Pi boots). The check
  is the same one that watches the speaker (`SPEAKER_WATCH_INTERVAL_SEC`,
  10s by default on a new install). The **first connection since the Pi
  (or the service) started** always starts the music - the speaker
  already there at boot, or turned on minutes later - whatever time of
  day it is and even if the music already played earlier that day; a
  `0` there falls back to the default in this mode rather than silently
  disabling it. After that first time, the trigger fires **once a
  day**, like `scheduled`: a reconnection while the music is already
  playing changes nothing, and one after a non-looping list has
  finished does *not* restart it, so a speaker that keeps dropping off
  and coming back cannot start music in the middle of the night. A
  click always starts it early.

**When the speaker goes away** (Bluetooth card, "If the speaker
disconnects"), all driven by that same check:

- `SPEAKER_LOSS_PAUSE` (on by default): the song is paused when the
  speaker disconnects and resumes when it comes back, rising over
  `SPEAKER_RESUME_FADE_SEC` seconds (0 = straight back). Only a song is
  paused - an announcement plays to its end - and a pause you chose
  yourself is left alone. A button press while the speaker is away does
  **not** put the music back on the air either: its sound is played (and
  heard if the speaker comes back mid-sound), the next song is loaded, and
  it stays paused until the speaker really is there. Before, a press undid
  that pause and the radio played on into nothing for as long as the speaker
  was gone. The radio checks the speaker every `SPEAKER_WATCH_INTERVAL_SEC`
  (10 s), and at once whenever an open page sees it come or go - so with the
  interface open, the pause follows the "not connected" banner.
- `SPEAKER_QUICK_STEP` (20, 0 = off): for 10 seconds after the music
  starts - or the speaker comes back - a press on the speaker's own volume
  buttons moves the volume by 20 points in the press's direction, since a
  speaker woken from standby is often far too loud and its own steps are
  small. Each press keeps the big steps 10 more seconds; then the speaker's
  steps are its own again. Works whether or not the speaker's volume is
  linked to the radio's (a lock wins over it).
- `SPEAKER_BATTERY_LOW` (15 %, 0 = off): when the speaker reports its
  battery to BlueZ, the level shows next to its address, and once it falls
  to that percentage the radio plays `BATTERY_LOW_SOUND` (a System sound)
  and the player says so - once per discharge, given back after the speaker
  has charged 10 points above the threshold. Each ten-percent step is kept
  in the event log. Many speakers do not report their battery at all; the
  line then simply does not appear.
- `AUDIO_FALLBACK_OUTPUT` (Audio output card, "If the speaker is lost"):
  instead of pausing, keep playing on a wired output (`jack`, `usb`,
  `hdmi`) while the speaker is away; it takes the sound back when it
  returns. Empty (the default) keeps the pause above. If that output is not
  plugged in, the speaker loss is handled as usual.
- `SPEAKER_LOSS_SHUTDOWN_MIN`: power the Pi off after the speaker has
  been gone that many minutes (0 = never).
- `SPEAKER_ABSENT_SHUTDOWN_MIN`: power it off when no speaker has
  connected that many minutes after startup (0 = never).

The **activity LED** (the green light on the board) can be switched off
from the System card (`ACT_LED`: `default` / `off`); the setting is
re-applied at every boot by `rukebox-act-led.service`.

Like every other timed event here, `scheduled` depends on the clock
being right, so it wants the RTC module (see the Clock section). The
trigger fires once a day. `bluetooth` needs no clock at all — but it
does need a speaker paired and its MAC address configured
(`SPEAKER_MAC`, see the Bluetooth section), since that is what it
watches.

**In all the silent modes** (`action`, `scheduled`, `bluetooth`), to
prevent the Bluetooth speaker from auto-powering off due to silence
detection (common behavior on this type of device), a very quiet looping
sound (`KEEPALIVE_SOUND`, provided in `assets/sounds/keepalive.wav`)
plays throughout the waiting period. It stops as soon as music actually
starts.

The same keep-alive mechanism also covers the **"stopped"** mode
reached when `MUSIC_LOOP=false` and the list finishes a full pass: the
Pi and speaker stay fully on (so a scheduled morning/cutoff/custom
announcement can still fire normally, and correctly returns to
"stopped" afterward rather than resuming music) until a single click,
or "Start music" in the web interface, restarts the list. The one
exception is the cutoff announcement: it shuts the Pi down exactly as
it would from any other mode, "stopped" included.

### Schedules: other hours, other settings, on chosen days

**Settings > Schedules** holds a list of schedules above the **Every day**
settings (the start mode and the cutoff described above, which keep applying
outside any schedule). A schedule has a name and:

- **Days**: every day, chosen weekdays, or one date. A date beats the
  weekdays; when two weekly schedules overlap, the first of the list wins.
- **Starts the music at** and **Stops at**: one, the other, or both. A start
  only starts the music when nothing is playing - like the daily start, it is
  a floor, not a leash. A stop at or before the start is the next morning
  (22:00 to 01:30 is one evening). Without a stop the schedule runs to the end
  of its day; a schedule with only a stop is just that stop.
- **At the stop**: pause the music, standby, or switch the Pi off.
- **Opens with** (only with a start): one of your announcements, played
  before the first song - one file, like every other trigger.
- **Volume** and **Music** (unchanged, the whole library, or one of your
  lists): set when the schedule begins. When it ends, the usual volume and
  the list that was playing before come back.
- **Settings during this schedule** (detailed view): pick any playback,
  volume, fade, button or cutoff setting and give it another value. It
  replaces the usual one from the start to the stop and is taken back
  afterwards; saving the usual settings meanwhile does not disturb the
  running schedule. The cutoff time is one of them, which is how an evening
  can run past the daily cutoff.

The daemon decides by the clock alone at every scheduler tick (15 s), so a
restart in the middle of a schedule finds it running again - its settings,
volume and list are applied, but a start that is already past is not
replayed. The player says which schedule runs and until when; while nothing
plays, it says which one comes next. Schedules live in
`/etc/rukebox/schedules.json` (`paths.schedules_file`) and need no restart.
They travel with the configuration export.

The arrows of a row move it up or down - the order matters when two weekly
schedules overlap - and **Duplicate** opens the form on a copy (an
announcement has the same button; the copy shares the folder of the first).
Under the list, **This week** draws the seven coming days hour by hour: one
bar per schedule, and a mark at the daily cutoff.

The daily cutoff itself can be switched off (**Every day** > **Daily
cutoff**, `schedule.cutoff_enabled`): the radio then only stops when a
schedule, a button or you say so.

### The talking clock

The radio can say the time out loud, offline, through `pico2wave`
(`libttspico-utils`) or, for Dutch and as a fallback, `espeak-ng` - both
installed with the radio, and by the updater when they are missing.

- **A button**: "Say the time" is one more click action (single, double, the
  speaker's buttons). The song pauses, the time is said, and the song goes on
  where it was. With nothing playing, it is simply said.
- **An announcement**: **Say first** (the time, or the time and the date)
  is spoken before its sound - "It is 6:58. Today is Monday, October 5." -
  and an announcement with no sound in its folder just says it.
- **Before the cutoff**: **Say it before (min)** (`schedule.cutoff_warning_min`,
  0 = off) says "The radio stops in 10 minutes" once, while the music plays.

**Music under announcements** (Settings > Fades,
`fades.music_under_announcements`, 0 = off): instead of pausing for an
announcement - the custom ones, the Announcement button, the spoken time -
the music goes on at that percentage of its volume while the announcement
plays in a second player, then comes back up. Skip stops the announcement.
The cutoff still stops the music, and a button's sound still plays alone.

The language is **Spoken language** (`schedule.speech_language`: en, fr, de,
es, it, nl), next to the cutoff, with a button to hear it. The setup page sets
it to the language it was used in. What is said never counts in the
statistics' top sounds; the event log keeps one "Spoken" line each time.

### The radio host, dedications and reminders

The same voice can do three more things.

- **Introduce the songs** (Settings > Schedules, Every day, **Introduce the songs
  (every N)**, `schedule.dj_announce_every`, 0 = never): every N songs, the
  next one is announced between the two - "Up next: Paradise, by Coldplay".
  Only when a song ends by itself: a "next" asked for goes straight to the
  song, and the first song of a start (which fades in from silence) is not
  introduced.
- **Dedications** (Network > Guest access, **Dedications**,
  `security.dedications`, off by default): whoever puts a song up next is
  asked for an optional message, said just before the song - "A dedication
  from Blue fox: happy birthday Marie. Here is Fly." The name is the one the
  device chose in the suggestion box. Up next shows the message under its
  song, and the owner can remove it before it is said. A message is at most
  160 characters, on one line.
- **Reminders** (Home > Reminders, owner only): a sentence to say in N
  minutes or at a time, within the next seven days - "Reminder: take the
  cake out of the oven." The music goes on under it at the level of **Music
  under announcements**, or at 25 % when that is off. A reminder missed by
  more than ten minutes (the radio was off) is dropped rather than said late.
  Twenty can wait at once; they survive a restart.

## Admin web interface

A complete web interface, accessible from your phone (or any device),
lets you control the radio remotely: volume, actions equivalent to the
Flic button, settings, clock, Bluetooth speaker management, SSH toggle.

### How to reach it without an existing network

Since the Pi has no Wi-Fi to an external network, it creates **its own
local Wi-Fi access point** — set up automatically by `install.sh` (see
"Admin access point" above), so there's normally nothing to do here:
your phone connects to it once manually, then reconnects automatically
afterward whenever in range (standard behavior for any Wi-Fi network
already saved on a smartphone).

1. On your phone, connect to the access point (SSID chosen during
   install, default `Rukebox-Admin`).
2. Your phone shows its usual "sign in to this network" prompt a second
   or two later — accept it and the interface opens. That prompt is the
   captive portal (`CAPTIVE_PORTAL_ENABLED`, on by default), and it is
   the reason there is no address to type on a device with no screen.
3. If the prompt doesn't appear (some phones suppress it on a network
   they already know), open `http://<Pi's IP shown at the end of
   install.sh>` — typically `http://10.42.0.1` with NetworkManager, or
   `http://192.168.4.1` with the older hostapd/dnsmasq fallback. No port
   suffix: the interface listens on port 80 (`WEB_PORT`). Set `WEB_PORT`
   to something else and the address needs `:<port>` again — and the
   captive portal then starts a second, tiny listener on port 80 whose
   only job is to redirect there, since a phone's probe only ever looks
   at port 80.
4. Tap **Finish connecting** on that page. This is what tells your
   phone the network is a normal one: until it happens, the phone keeps
   treating the Wi-Fi as "not really connected" and drops it after a few
   minutes, closing the portal window with it. Afterwards the window can
   be closed safely and the Pi stays reachable in an ordinary browser.
   While the portal still holds the device the interface opens on its
   **Home menu** rather than on "Now playing", and the button is part of
   that menu: on a phone it waits at the foot of the Home grid - the only
   grid that offers it, since that is the menu the interface opens on -
   and on a computer it sits at the foot of the sidebar, on every page.
   The sentence that follows a release ("this network is now marked as
   connected") is a confirmation, not a banner: it shows **once, for ten
   seconds**, in the tab that arrives after the tap, and a refresh does not
   bring it back. Tapping it also hands the
   interface to your ordinary browser and sends the portal window to the
   address your phone probes, which is what lets the phone mark the
   network as connected and take that window away by itself - if yours
   keeps it open, just close it, the network is yours already.
   The tap is remembered **against the device itself, for 12 hours, and
   written down**: an update, a service restart or the Pi's daily reboot
   no longer asks a device that has already finished to finish again.
   **Connected devices** shows it per device and offers *Show the portal
   again* to undo it.
5. On subsequent occasions, the phone joins this network on its own
   whenever in range — the interface is then directly reachable.

> **Why the page cannot simply open in your usual browser.** The window
> a phone shows for a Wi-Fi portal is its own, built into the operating
> system, and nothing in the page can move itself out of it — that is
> true of hotel and train portals too. What "Finish connecting" buys is
> that leaving that window no longer costs you the network.

**Three modes**, in Network → Guest access (`CAPTIVE_PORTAL_MODE`):

- **Let devices through once they open the page** (default): the
  behaviour above.
- **New devices only**: a device that has opened the page once is never
  held again.
- **Keep showing the page at every check**: the portal page is pushed
  again on every connectivity check. Useful if you want it in front of
  people every time, at the cost of the phone never settling on the
  network.

Each device can also have its own rule (Network > Connected devices).

To (re)create it by hand instead (same script `install.sh` already
calls; also useful if you skipped the prompt during a non-interactive
install):

```bash
sudo /opt/rukebox/scripts/setup_ap.sh "My Network" "a-strong-password"
```

### If the Bluetooth sound skips

On a Pi Zero, Wi-Fi (the access point and your personal network) and
Bluetooth share one radio. Rukebox therefore installs the audio server
(PipeWire and WirePlumber) with realtime priority - `rtkit` alone is not
enough, the session also needs its own limits, which the installer sets -
turns Wi-Fi power saving off on its connections, and slows uploads down
while a sound plays to a **connected** speaker of the built-in chip: music
or an announcement, but never the keep-alive chime (which plays in the
silence, where slowing a transfer down would serve nobody) and never in
pause (**Audio > Audio output > Limit transfers**, 200 KB/s by default,
`auto`). A sync done with the speaker off, or while paused, runs at full
speed.

A USB Bluetooth dongle is a radio of its own, but it is not automatically
the cure: a few centimetres from the board's antenna it can disturb the
Wi-Fi as much, and on the radio this was measured on, the stream cut out
more with the speaker on the dongle than on the built-in chip (the Flic
button went to the dongle instead). If your speaker is on a dongle and
transfers still make it stutter, **Also with a speaker on a USB dongle**
(`transfer_limit_usb`, under the same setting) applies the limit there too.

That sharing has a second face, measured while streaming to a speaker: the
**Wi-Fi link itself gets flaky** — SSH sessions time out, and a file
transfer can arrive truncated (an update pushed by hand during playback left
a source file empty and the daemon refused to start until it was sent
again). `scripts/update.sh` protects you here: it checks that every file at
the other end hashes to what was sent, and refuses code that does not
import, before it swaps anything in. A hand `scp` does not — if you copy
files onto the Pi yourself, stop the music first, or check the file sizes
afterwards.

### Checking everything with the speaker: the test routine

What only a real speaker can prove - the sound coming out, the linked volume
and its hand-over when the speaker comes back, the volume lock, the speaker's
own buttons, the pause when it is switched off, a system sound over the music,
a scheduled announcement followed by its action, a whole schedule, resuming at
the same position - is walked through by one script, in French, from the
computer: `bootstrap\hardware_check.cmd` on Windows,
`bootstrap/hardware_check.sh` elsewhere (the Pi's address as an optional
argument, `rukebox.local` by default). Each test says what it will do and what
you will have to do; **Enter** runs it, **P** skips it, **Q** stops.

Everything it changes is saved first (`~/rukebox-tests/avant-<date>.json` on
the Pi) and put back at the end, after Q, Ctrl-C or a dropped connection; the
temporary announcement and schedule are deleted. If the script itself was
killed, `python3 /opt/rukebox/scripts/hardware_check.py --restore <that file>`
puts the settings back. The results are written next to it
(`resultats-<date>.txt`).

### If the sound stops for no reason: the audio diagnostic

A Bluetooth link can carry nothing at all while every layer above it says it
is fine: BlueZ reports the speaker as connected, mpv goes on playing, and the
radio makes no sound because PipeWire has moved the stream to its Dummy
Output. `bt-connect.sh` notices it (the controller's own byte counter stops
moving) and repairs the radio, which takes about half a minute - and the music
used to keep "playing" silently through it. It now **pauses** and says so, and
the event log gets a *Speaker link silent* line.

To see exactly what the path was made of - the output, the codec, the kernel's
own radio timeouts, the bitrate the link really carries over a few seconds -
use **System > System health > Audio diagnostic**. The report can be copied
with one button; it is what to send if the sound cuts out again. Over SSH, the
same report is `sudo /opt/rukebox/scripts/audio-check.sh`.

What it usually shows, in order of likelihood:

- **`hci0: n kernel timeout(s)`** - the radio stopped answering the kernel,
  which is what wedges a link. `bt-connect.sh` repairs it by itself.
- **The default output is not the speaker** - the sink left and the music is
  playing into nothing.
- **`bluetooth codec: sbc` with about 200-250 kbit/s** - the common case: SBC
  at bitpool 35, the lowest rung. This is the *speaker* choosing it. Offering
  SBC-XQ alone makes a speaker that does not support it answer on the headset
  profile instead (telephone quality), and PipeWire on Raspberry Pi OS has no
  AAC encoder - so a Bluetooth speaker that does LDAC or aptX is the only way
  to a better link. **Which codecs the Pi offers is a setting** (*Audio output >
  Bluetooth codecs offered*): the row shows what is offered now and opens the
  list, where you tick only what the speaker accepts (the last one cannot be
  unticked - offering none leaves the speaker nothing to negotiate). Saving it
  writes a WirePlumber drop-in (`/etc/wireplumber/wireplumber.conf.d/
  20-rukebox-codecs.conf`) and restarts the audio service - the speaker may take
  a few seconds to come back, and the diagnostic then says which codec the link
  really ended up using.
- **`mpv filters: acompressor, volume, alimiter`** - the **Volume boost** is
  on. It makes quiet recordings audible, and it does squash dynamics: set it
  back to *Off* to hear what the speaker really does.
- **`Limit transfers` with `Limited speed`** (*Audio output*): a big upload
  (music sync, a sound file, a backup) takes the radio for itself and the
  Bluetooth sound can stutter, so the Pi reads those uploads slowly on purpose
  - a list to choose from (64 to 512 KB/s), not a number to type. *While music
  plays over built-in Bluetooth* is the default: it does nothing when nothing
  is playing, and nothing when the speaker is on a USB dongle unless **Also
  with a speaker on a USB dongle** is on.
- **`its own volume 0.4`** - the speaker's own AVRCP volume is attenuating
  everything before it is amplified, so the interface's slider only has that
  much range to work with. Raise it once with
  `wpctl set-volume @DEFAULT_AUDIO_SINK@ 1.0`.

### Duplicate tracks: what the catalogue already knows

*Home > Duplicate tracks* lists the songs the library holds more than once.
The check runs twice: once when the interface starts, and again every time the
page is opened. The page takes its place in Home only when it has something to
say, and it costs nothing to run: the scan already read every file once, so
this is a query over the catalogue (title, artist, album, size, duration) and
it never touches the card.

Two spellings of one song are the same song - brackets, a leading track number
and a "feat." clause come off before comparing, so "05 - La Seine" and
"13 - La Seine (Extrait de la bande originale)" end up in one group. Inside a
group, a copy marked **same file, twice** weighs exactly what another one
weighs, to the second: nothing but a real copy does that, and it is the group a
reader can act on without listening.

Each copy shows its album, its size, its length and the bitrate its own bytes
imply, with a **play** button so two of them can be compared by ear and, beside
it, **Keep this one** - a square on a phone, where its words have no room and
the symbol alone says what it does. Keeping one copy sets the others aside:
they leave what the radio chooses by itself and are struck through here, but
**no file is ever deleted** - the song is still there to be played by hand, and
*Give it back* puts it straight back in the rotation.

Nothing is hashed. On a Pi Zero, reading 12 GB of music to compare it would
cost ten minutes of card access and say nothing more, since two files of the
same size and the same length to the second are copies. Two different masters,
a re-encode, a live version: they are still grouped, and the sizes and the
bitrates are what tells them apart.

### Using the USB port for devices instead

A Pi Zero has a single USB data port. By default it is the network link
to a computer described below. To plug devices in instead - a Bluetooth
dongle (the speaker on one controller and the Flic button on the other), a
USB sound card or a USB key - set **System > USB port**
to **USB devices** (`usb_port_mode: "host"` under `hardware:`), then restart
the Pi when offered. Use an OTG adapter on the port marked USB.

- The cable access (169.254.7.7, Internet through the computer) stops:
  keep a way in over Wi-Fi (`rukebox.local` on your network, or the
  access point at 10.42.0.1) before switching.
- `rukebox-usb-gadget.service` applies the choice: no gadget, and
  `dtoverlay=dwc2,dr_mode=host` on the line under `# rukebox: USB gadget
  mode` in `config.txt` (back to `otg` for the network mode).
- The System health card lists the USB devices the Pi sees - the quick
  way to check that a dongle is detected.

### Over the USB cable

With the Pi plugged into a computer (the USB gadget, see "First boot and
connection"), the cable carries **two network cards**:

| Card on the computer | Pi address | For |
|---|---|---|
| "Rukebox (USB)" (MAC `02:1a:11:00:00:01`) | `169.254.7.7` | SSH and the web interface |
| "Rukebox (Internet)" (MAC `02:1a:11:00:01:01`) | `192.168.77.2` | Internet for the Pi, through the computer |

The interface is at **http://169.254.7.7/**. The Pi always has that
address; the computer takes one of its own in `169.254.x.x` by itself
(which can take up to a minute the first time).

**Do not use Windows' "Internet Connection Sharing"** towards the Pi: it
forces the card it shares to onto `192.168.137.1`, a network the Pi is
not on, and SSH works while the web interface does not - the classic
symptom. Use the second card instead (below).

**Names.** "Ethernet 5" and the like are labels Windows picks itself; the
Pi cannot set them (what it announces over USB - "Rukebox USB", serial
`rukebox0001` - shows on the USB device in Device Manager, while the
network cards are named by Windows' own driver). `usb_internet.cmd`
below names both cards; by hand it is
`Rename-NetAdapter -Name "Ethernet 5" -NewName "Rukebox (USB)"` from an
administrator terminal. The names stick: Windows ties them to the MAC
addresses, which the Pi always keeps the same.

### Internet over the USB cable

The Pi's second USB card (`usb1`, `192.168.77.2`) uses the computer
(`192.168.77.1`) as its way out. To set the computer's side up, once:

- **Windows**: double-click `bootstrap\usb_internet.cmd` (it asks for
  administrator rights). It names the two cards, gives the Internet one
  its address, creates a Windows NAT (`New-NetNat`) for
  `192.168.77.0/24`, and turns off Internet Connection Sharing if it was
  pointed at a Rukebox card. It follows whatever connection the computer
  uses (Wi-Fi or Ethernet) and survives reboots.
  `bootstrap\usb_internet.cmd -Remove` undoes it.
- **Linux**: `bash bootstrap/usb_internet_linux.sh` (asks for sudo).
  Finds the card by its MAC (its name depends on the distribution),
  adds 192.168.77.1 to it, turns IPv4 forwarding on and adds one
  masquerade rule (`iptables`, or a table of its own with `nft`) - plus
  two forward rules only if the forward policy is DROP, as Docker sets
  it. If NetworkManager runs, it is told to leave that card alone until
  the next reboot. Nothing is permanent: a reboot undoes it, and so does
  `--remove` (which also restores the previous forwarding setting).
- **macOS**: `bash bootstrap/usb_internet_macos.sh` (asks for your
  password). Adds 192.168.77.1 to the card as an extra address, turns
  forwarding on, and loads one NAT rule into an anchor of its own inside
  Apple's firewall ruleset (`com.apple/rukebox`) - macOS's own rules are
  not replaced, and pf is enabled with a reference that `--remove`
  releases. Nothing is permanent either. The rule names the Mac's current
  connection: run it again after switching between Wi-Fi and Ethernet.

The Linux script was run end to end (set up, run twice, `--remove`,
card absent) on a Debian with `nft`, against a stand-in card carrying
the Pi's MAC; its `iptables` branch and the macOS script have not been
run on a real machine yet.

Nothing on the Pi depends on it: without it the card simply leads
nowhere, and the home Wi-Fi, when there is one, stays the preferred way
out (lower route metric). Verified on the real Pi: ping, DNS and HTTPS
all go through `usb1` with the Windows NAT, while `169.254.7.7` keeps
answering SSH and the web interface.

A Pi updated from an older version gets the second card the next time
the gadget is built (at boot, or `sudo systemctl restart
rukebox-usb-gadget` - which briefly drops the USB link).

### Taking turns in the queue

With **Take turns between people** (Network > Guest access,
`security.queue_fair`, on by default) the songs asked for alternate between
people - one each, then the next round - instead of first come, first
served: three songs from Anne then one from Bob play as Anne, Bob, Anne,
Anne. A person is a device, or the devices linked as one person.

### Guest access

With a password set, anyone joining the access point normally sees only
a login box. Turning on **Guest actions without password** (Network →
Guest access, `GUEST_MODE_ENABLED`) gives them the Home pages instead:
**Now Playing, the volume and the button actions** (sound + next track,
previous, pause and resume, skipping a sound, announcement, start the
music), Up next, the Library to put a song up next, Recently played,
Today and the suggestions. Each action can cost credits, and any of them
can be **locked** for guests altogether - see below.

Not included, and not reachable by any route: **shutting the Pi down**,
the loop, the timers, and every setting, statistic and network control.
Those stay behind the password, and a **Log in for full access** button on
the same page swaps the guest view for the whole interface.

It is **off by default**. It only changes anything while a password is
set — with none, the whole interface is already open to anyone on the
access point — so switching it on is a deliberate decision about what a
visitor may do, and this project would rather you made it than have an
update make it for you.

**Pages hidden from guests** (Network > Guest access, `GUEST_PAGES_OFF`):
tick the Home pages a guest should not get - Up next, Recently played,
Today, Library, Blind test, Suggestions. The page leaves the guest's menu and
the server refuses what it asks for; Now playing always stays, and the owner
still sees everything.

**Vote to skip** (Network > Guest access, on by default): as soon as two
people have the page open, the player offers "Vote to skip (1/3)". A vote is
free and counts once per person (linked devices are one person) and per
song; when **more** than `SKIP_VOTE_SHARE` percent (50 by default) of the
people with the page open have voted - two votes at least - the next song
plays and the event log says "Skipped by vote". Who is "here" is who made a
request in the last two minutes, i.e. anyone whose page is open; a new song
starts a new vote.

**Blind test** (Home > Blind test, guests included): the owner picks a
number of rounds (5 to 20) and an extract length (10 to 30 s) and starts a
game. The radio steps aside - the song playing pauses after the usual short
fade - and each round plays an extract of a song from the library, taken
about a third of the way in, while everyone with the page open picks the song
among four (title - artist, the three wrong ones by other artists where the
library allows). A right answer scores one point, the fastest right answer
two. A round ends when its extract is over or when everyone looking at the
game has answered; the right song is then shown for a few seconds, with the
scores. At the end, or when the owner stops it, the radio takes up the song
it had paused (or goes back to waiting). Players are named like in the
suggestion box. Only songs whose tags have been read, with a title and an
artist, are asked about; a library with fewer than the rounds asked for
cannot start a game. The extracts never count in the statistics; the event
log keeps "Blind test started" and who won it.

### Features

- **Finding your way**: the bar at the bottom (a column on a wider screen)
  holds six areas — Home (listening, the library, adding music), Settings
  (how the radio behaves: start and cutoff, playback, volume, fades, buttons,
  announcements), Audio (where the sound goes), Network (the access point,
  the devices on it, guest access, personal Wi-Fi), System (the Pi itself:
  health, clock, password, updates) and Stats. Each area holds
  **pages**, one card each. On a phone, tapping an area shows its pages as a
  **grid** — a cell per page, an icon and a title, no frame around it — and
  the **logo** at the top left, darkened with an arrow on it, brings that grid
  back. A medium-width window (from 640px) keeps that grid and puts the areas
  in a column of icons on the left; from 1024px that column has room for names,
  and the same pages are listed **under their area** there, one click away,
  with a small **what is playing** box at its foot that leads to the player
  (and, on a device the portal still holds, **Finish connecting**). That
  foot stays put: a long list of pages scrolls above it and fades out where
  it is cut, instead of passing under it. **Now playing** is a page of its
  own, and the one you arrive on. On a phone narrower than 390px the bottom
  bar shows its icons only, with the name of the area in use, and the
  theme, language and logout buttons move into one **more options** menu
  (three dots) at the top right. On a phone a page uses the whole width — no card frame, no shadow,
  no padding, because that width is what its content needs; from 640px the card
  comes back, with the rail beside it. A page has its own address, so it can be
  linked to and the browser's
  Back button works: `#home/library`, `#settings/announcements`,
  `#audio/bluetooth`. A guest has no bar at all, so for them the grid *is* the
  menu — and "Restricted access" is written on that grid, not on the pages.
- **Now playing**: current mode, current track, volume (real-time
  slider).
- **Actions**: the same as the Flic button — sound + next track,
  announcement, start (if idle), stop + shutdown (with confirmation,
  destructive action). **Announcement** asks which one to play: the
  button sounds, the cutoff, or any announcement of your own (one file,
  the next in its order). The physical button cannot ask, so it plays
  whatever its own action says; here there is no reason not to ask.
  Whichever you pick, the music resumes straight afterwards — choosing
  the cutoff previews what it sounds like, it never shuts the Pi down.
- **Results appear as a small bar at the bottom of the screen** for five
  seconds — what happened on the first line, why on a second, quieter
  one — rather than a dialog you have to dismiss. Tap it to clear it
  early. Confirmations still ask properly, because those need an answer.
- **Audio output** (Audio tab): the Bluetooth speaker, or a wired output
  of the Pi - the headphone jack (Raspberry Pi 3/4), a USB sound card or
  HDMI (`audio: output`). What the Pi found is shown under the choice, and
  **Test** plays a short chime through it. A wired output is found again
  whenever it is plugged in; while it is missing, the sound goes to the
  default output. With a wired output, a Bluetooth speaker that drops no
  longer pauses the music nor powers the Pi off.
- **Skip a sound** (Home): while an announcement or a button sound
  plays, the Pause button reads **Skip** - it ends that sound at once and
  the music carries on, as if it had finished (never the cutoff).
- **Up next** (Home): the next songs, in the order they will come
  (songs asked for first, in the order they were asked, marked "Asked
  for"; then the queue or the loop), each playable at once. How many:
  `upcoming_tracks` (10 by default, 0 hides it).
- **Recently played** (Home): the last tracks played, newest first, with
  their title and artist (guests see it too). How many: `recent_tracks`
  (20 by default, 0 hides it), in the Playback card's detailed view.
- **Liked tracks** (Home, not for guests): the heart on the cover of Now
  Playing marks the song playing, and this page lists what has been liked,
  newest first, **with the day it was liked**; each line puts the song back
  in the queue ("Next") or takes the like back (the red heart). A like is
  kept by the track's library key, so it survives a rescan, and the file is
  `likes_file` (`/var/lib/rukebox/likes.json`) - plain JSON you can copy to
  another Pi. The page only appears once something has been liked.
- **On this day** (top of Up next, not for guests): the songs liked on
  this day in an earlier year ("A year ago, you liked: ..."), two days
  either way, else those liked a month ago - each with "Next" to play it
  again. Nothing shows when no like falls on the date.
- **Today** (Home, guests too): music time, songs, sounds and
  announcements, button presses and the most played songs of the day.
- **Mute**: the speaker icon left of the volume slider mutes and unmutes
  (the volume stays where it was); also a button action ("Mute / unmute").
- **Standby or switch off**: the button at the bottom of Now Playing asks
  which. Standby stops the music with a fade and keeps the Pi on, waiting
  like at startup; the song that was playing comes back first. Both are
  also button actions.
- **Library** (Home): search the music by words, or by artist, album or
  genre, then put a song up next ("Next", also in Recently played): it
  plays after the current one and the songs already asked for, or at once
  when nothing plays.
  Each row also carries one **heart-and-plus** button, which asks what the
  song should collect: a **like** (the heart in it turns red once the song is
  liked, exactly like the one on the cover) or **a place in a list**. Two
  answers, one symbol, and nothing to open first.
  The Pi reads each file's tags once, in the background (about one file a
  second on a Pi Zero), so a first catalogue takes a few minutes; until
  then songs are named after their folders. A music suggestion that is
  already in the library says so - before it is sent, and on its row,
  with a button to play it.
- **Lists** (Home): what the radio plays - everything, or one list at a
  time. A list is either the songs you add to it by hand (the **+♥**
  beside a Library song), or everything the library tags with the genres you
  tick - kept up to date on its own. **Play** starts a list at once,
  **Play everything** puts the whole library back, and the choice survives
  a reboot. See "Your own lists" below.
- **Guests see what an action costs** as a small badge on its button, red
  when their credits do not cover it. Buttons **vibrate** under the finger
  on phones that allow it (not iPhones); a switch in System turns it off
  for that browser.
- **Suggestions** (Home): anyone who can open the interface - guests
  included - suggests music or an announcement to add, under a name no
  other device can take, and votes the others for or against (one vote
  per suggestion and per device, recognised by a browser cookie and by
  its MAC address; the vote can be changed or taken back). A music idea
  is one line (200 characters), an announcement idea a few lines (500).
  A device may change its name once an hour (`rename_interval_min`). The
  owner marks suggestions added or declined, deletes them, and sees
  everyone who took a name with all the names they went by. New ones are
  announced to the owner only: a red count on the Suggestions tile, on the
  Home tab and in the side menu until the page is opened, and a bubble
  with a **See** button when one arrives while the interface is open.
  What was already seen is remembered per browser; the first visit of a
  browser counts from that moment. Can be turned off (Network > Guest
  access, with the delay).
- **Simple or detailed view**: the interface opens in a simple view with
  only the essential options. The box at the bottom of each section
  ("Show all options") switches to the detailed view, with everything;
  the same box brings the simple view back. Remembered per browser.
- **Explanations on demand**: each section heading carries a small
  **?** button. The interface stays uncluttered by default and the
  explanatory text appears only when asked for. Status lines (what was
  just saved, whether the access point is up, a test result) are not
  part of this and are always shown.
- **Schedules** (Settings): the schedules, then **Every day** - when the
  music starts (at boot, on a click, at a set time, when the speaker
  connects), the daily cutoff and what it ends with (the morning
  announcement's time is edited in the Custom Announcements card).
- **Playback**, **Volume** and **Fades** (Settings): order modes, loop,
  progress and resume, timer durations, unreadable tracks; base volume,
  volume mode, boost and the speaker's own volume; the four fades.
- **Button actions**: what each click (Flic, GPIO button, the speaker's
  own buttons) does, and the sound played before the next track.
  Changes apply as soon as they are saved; the few that need a restart
  say so, with a button in the confirmation.
- **Announcement files**: pick an announcement, add or delete its sounds,
  listen to them, and reorder them with up/down arrows — the order is only
  used when the announcement order mode is `ordered`. Applies immediately.
- **Clock** (System): status (RTC detected / fallback / waiting), manual date
  and time setting, **timezone** (shown, and changeable — see "The
  timezone" below), configuration and **immediate test** of the
  Bluetooth fallback (same mechanism as `scripts/setup_bt_clock.sh`,
  directly from the phone).
- **Bluetooth** (Audio): connection status, connect/disconnect, scan
  for nearby devices and pair them, set one as the main speaker or as
  the clock source. A scan lists the devices that announce a name (plus
  any that is already paired or connected); the ones that do not are
  hidden, because a list of bare MAC addresses cannot be acted on.
- **Configuration**: download every setting, the custom announcements,
  the saved track orders, the schedules, the music lists, the liked tracks
  and the tracks set aside as one JSON file, and import one — see
  "Backing up the configuration" below.
- **System**: enable/disable the SSH server, trigger a music library
  rescan.
- **Statistics**: everything the radio has actually done — see the
  section below.

### Covers and lyrics

The Home card shows the cover of the track being played, and a
**Lyrics** button when it has lyrics. Nothing to configure: they are
looked up next to the track, most specific first.

- **Cover**: a picture with the same name as the track
  (`03 - Mojo.jpg` beside `03 - Mojo.opus`), then the picture embedded in
  the file itself (MP3, FLAC, Ogg/Opus, M4A...), then the folder's
  `cover.jpg` / `folder.jpg` / `front.jpg`.
- **Lyrics**: a file with the same name as the track - `.lrc`
  (synchronised; "enhanced" per-word timings are accepted), `.srt` or
  `.vtt` (subtitles, synchronised too), `.txt` (plain text) - then the
  lyrics embedded in the file's tags. Synchronised lyrics follow the song:
  the line being sung is highlighted and kept in the middle; scrolling by
  hand pauses that for a few seconds.

Reading embedded pictures and tags uses `ffmpeg`/`ffprobe`, which
`install.sh` already installs. Each track is looked up once and cached.

### Theme, language and interface

Light theme, dark theme, or automatically following system preferences
(one button, top right, toggling light/dark — it follows the system
until you tap it once, then remembers your explicit choice from then
on). Next to it, a language button (two-letter code, e.g. `EN`) cycles
through English, French, German, Spanish, Italian and Dutch — same
behavior: follows the browser's own language until tapped once, then
remembers the explicit choice. Both choices are remembered per device,
not shared between phones. The Pi only sends the translations a page
reads - English, which every missing text falls back to, and the page's
own language - and fetches another one when the language changes, without
reloading the page: the first load is about 140 KB lighter for it.

The page also only asks the Pi for what is on screen: a card's data is
refreshed while its page is shown, and brought up to date the moment that
page opens, so an open tab costs the Pi almost nothing while it shows the
player.

### Security

**No authentication by default.** The core security assumption is that
the only way to reach this server is by being connected to the Pi's
local, private Wi-Fi access point — normally, someone needs to know the
hotspot's Wi-Fi password just to reach the interface **unless you chose
an open access point** (no password) during installation or from its
card in the web interface, in which case anyone in range can. **Never
expose it** on a shared network (work Wi-Fi, shared home network) or
the Internet without adding real authentication beforehand: as it
stands, anyone who can reach it could stop the music, shut down the Pi,
or enable SSH.

An **optional password** is available as a second layer on top of the
access point itself (useful if the AP's own password ends up shared
with guests, for example — or if the AP is open by choice) — set it
during installation, or from the **Security** card (System tab)
afterward. Leaving both fields empty and saving removes it again. The
password is never stored in plain text (a salted PBKDF2 hash,
`WEB_PASSWORD_HASH` in `rukebox.yaml`), and it is still exactly that: an
extra layer, not a replacement for the access point being private. A new
password needs at least 8 characters.

**Other websites cannot drive or read the interface.** A page open in the
browser of a device on the network could otherwise make that browser talk to
the Pi behind your back. So the interface refuses any command whose origin is
not the Pi itself, and only answers under its own address or its own name on a
local network (`rukebox`, `rukebox.local`, `.lan`, `.home`, `.internal`...). If
you reach the Pi under another name of your own, list it, or the interface
answers "this address is not one of the Rukebox's own names":

```bash
sudo python3 /opt/rukebox/src/config_file.py set WEB_EXTRA_HOSTS=radio.example
```
Guessing it is slowed down by the server itself: after 3 wrong passwords
from one device (its address), each further try locks it out for 5
seconds, then 10, 20... up to 5 minutes; the login screen counts the
wait down, and the right password clears it. The "current password" of
a password change counts the same way.

**Forgot the password, on a device with no screen or keyboard to reset
it any other way?** On a Raspberry Pi, grounding a GPIO pin at boot
clears it automatically — connect a jumper wire from **GPIO21** (BCM
numbering, physical header pin 40 — see the
[pinout](https://pinout.xyz/) for your model) to any `GND` pin, power
the Pi on, then remove the wire once it has booted. No resistor needed:
the pin is configured with its own internal pull-up. The pin number is
configurable (`gpio_reset_pin` in `rukebox.yaml`), and the whole feature
can be turned off with `gpio_reset_enabled: false`. On anything that
isn't a Raspberry Pi (or without the `pinctrl` tool, part of current
Raspberry Pi OS), this check quietly does nothing — there is no other
way to reset the password there than editing `rukebox.yaml` by hand
(clear `password_hash:` under `security:`).

## Configuration

Everything the radio can be told is one file: **`/etc/rukebox/rukebox.yaml`**.
Structured, commented, meant to be edited by hand over SSH, and it is
also exactly what the web interface's settings form reads and writes.

### Why YAML, and what the second file is

A plain `KEY=value` file was the original format; this project moved to
YAML for two reasons: it groups related settings under headings instead
of one long flat list, and it can carry the explanation for each setting
as a real comment above it rather than a separate document to keep in
sync.

Six systemd units (`rukebox-daemon`, `rukebox-web`, `flicd`, `flic-bridge`,
`bt-connect`, and this project's own tooling) read their configuration
via `EnvironmentFile=`, and two shell scripts `source` it directly —
neither speaks YAML, and neither is going to learn to. So a second file,
**`/etc/rukebox/rukebox.env`**, is generated from the YAML automatically and
is what those actually read. **Never edit `rukebox.env` by hand** — it is
overwritten every time `rukebox.yaml` changes, and says so at the top.

### When a change takes effect

- **Saving from the web interface** writes `rukebox.yaml` and regenerates
  `rukebox.env` immediately, in the same request — the file on disk is
  never stale.
- **A hand edit over SSH** is picked up the next time something reads
  the file: a `rukebox-config.service` unit runs at every boot to sync
  `rukebox.env` from whatever `rukebox.yaml` currently says (also covering
  the very first boot). To apply a hand edit without rebooting:
  ```bash
  sudo systemctl restart rukebox-config.service
  sudo systemctl restart rukebox-daemon.service rukebox-web.service
  ```
- **Saved from the web interface**, a setting reaches the running daemon
  at once (the interface tells it to read the file again); the few that are
  only read at startup say so, and the confirmation offers the restart.
  **A hand edit** is not announced to anyone: restart as above, or save any
  setting from the interface afterwards, which reloads them all.

### The web interface never loses a hand edit

Opening the settings form loads today's values once; from then on, the
page **only ever sends the fields you actually changed** back to the
server, and periodically refreshes any field you have *not* touched with
whatever is currently on disk. Concretely: if you edit `rukebox.yaml` over
SSH while the page happens to be open, and then save an unrelated field
from that page, your SSH edit survives — it was never part of what got
sent. A field you are actively typing into is never overwritten by that
background refresh either.

### PyYAML

Reading and writing `rukebox.yaml` uses PyYAML (`python3-yaml`, installed
by `scripts/install.sh`). If a Pi somehow ends up without it — an update
pushed over USB cannot run `apt` — the radio keeps running normally: it
falls back to reading the already-generated `rukebox.env`. Only a hand
edit made to `rukebox.yaml` *while PyYAML is missing* has to wait for
PyYAML to come back before it is seen; nothing is lost, it is simply not
read yet.

## Statistics

The radio records what it actually does, so questions like "did it
really start this morning?", "how much did I listen to last week?" or
"why did the music stop?" have an answer without reading through
`journalctl`. Everything is **consultable and resettable from the web
interface** (the Stats area), and never leaves the Pi.

### What is recorded

| Category | Recorded |
| --- | --- |
| **Button** | Every press (single / double / long), whether it came from the **Flic button, the GPIO button, the speaker's own buttons or the web interface**, and what it actually did: which sound was played, which track followed, which announcement ran — or why the press was ignored (an announcement was already playing, no sound available...). |
| **Startups / shutdowns** | Each daemon start with the time the Pi booted, and each shutdown with its cause: scheduled cutoff, long press, service stopped, or **power cut / crash** (detected because nobody was around to close the session). Also "launched" vs "actually used": a Pi that boots and plays nothing counts for the first only. |
| **Clock** | How the time was established each boot — hardware RTC, Bluetooth, set manually, or never established — and by how much the clock had to be corrected. |
| **Listening** | Real listening time per track, per sound and per announcement (measured from playback, not from file length), plus the per-day totals behind the chart. |
| **Announcements** | Which announcement file played and how many times, for each announcement. |
| **Playback errors** | Every file that failed to play, with mpv's error message, and which files fail most often. |
| **Speaker** | Whether the Bluetooth speaker dropped out **during** a session (powered itself off, went out of range) and whether it came back. |
| **Access point** | Devices associating with the admin Wi-Fi hotspot, and sessions opening the web interface. |
| **Configuration** | Settings changes, service restarts, SSH and personal Wi-Fi toggles, Bluetooth connections, volume changes. |

### Timestamps and the clock

This Pi has no NTP, so a timestamp taken before the clock has been
established means nothing. Rather than silently recording wrong times,
each event carries a "clock established" flag:

- events recorded before the time is known are shown **in italics with a
  `~`** in the log;
- as soon as the time *is* established (RTC, Bluetooth or manual), the
  timestamps of that boot are **retro-corrected** by the measured
  offset, and the day they belong to is recomputed.

That is why the boot times shown in "Startups & shutdowns" are the
corrected ones, as asked.

### Reading them

Open the **Stats** area of the web interface (detailed view), six pages:

- **Summary tiles** — cumulative totals since the beginning (or since
  the last reset);
- **Recap** — a period (this month, last month, this year, last year, since
  the start) in figures: the music played, the songs, the days with music
  and the artists; the most musical day; the song that opened the day most
  often; the songs liked; and the top five artists and songs. It reads a
  `monthly` table that counts each song month by month and is never pruned,
  and the per-day rollups, which are now kept three years whatever the
  retention (a few thousand small rows a year). An installation from before
  that table fills it once from the events it still keeps;
- **Listening per day** — bar chart over 7 / 14 / 30 days; a day that
  also produced playback errors is drawn in red;
- **Most played & announcements** — top tracks, and each announcement
  with how many times it ran, including the announcement types you
  added yourself;
- **Playback errors** — the files that keep failing;
- **Startups & shutdowns** — one line per boot: time, duration, cause,
  and how the clock was established;
- **Event log** — the raw chronological log, filterable by event type,
  loaded when its page is opened (it is the heaviest query).

### Deleting individual entries

Each of those four lists has a checkbox on every row and a **Delete
selected** button of its own, for the times a test file, a failed
experiment or a stray boot is cluttering a list you otherwise want to
keep.

Two things are worth knowing before using it:

- **Cumulative totals are not rewritten.** Deleting a row removes it
  from its list and nothing else; the summary tiles keep counting what
  actually happened. This is the same rule the retention window already
  follows — the totals survive old events being dropped — and deleting a
  log line is not a claim that the thing never happened.
- **The startup still in progress cannot be deleted** while it is
  running; it is the row the daemon is currently writing to. It has no
  checkbox, and the interface says so if you select it through some
  other route.

### Exporting and resetting

- **Download (JSON)** writes the whole thing to a file. Since the Pi has
  no network, this is the only way to keep a history off the machine
  before a reset or before the retention window drops old events.
- **Reset** asks which scope, as three buttons:
  - **Everything** — totals, history and event log;
  - **Totals and tallies only** — keeps the event log;
  - **Event log only** — keeps the totals.

  The reset goes through the daemon when it is running, so a session in
  progress is reopened immediately and whatever is playing keeps being
  counted.

### Storage and SD card wear

Statistics live in a single SQLite file (`/var/lib/rukebox/stats.db`).
**No third-party library is used** — `sqlite3` ships with Python and the
chart is plain CSS, so nothing had to be vendored into this repo and
nothing is ever fetched from the network. The card stays perfectly
usable offline, which is the whole point of this project.

Two guards keep the file from growing without bound:

- `STATS_RETENTION_DAYS` (90 by default) prunes the individual event
  rows. **Cumulative totals are never pruned**, so "60 hours of music
  since the beginning" stays true even once the events behind it are
  gone.
- `STATS_MAX_EVENTS` (20000) is a hard ceiling regardless of the
  retention, guarding against a pathological case such as a folder full
  of unreadable files.

Writes are deliberately sparse: roughly one row per track, per press and
per notable event. Volume changes are debounced (dragging the slider
does not write once per pixel), and the web interface only records one
"session" per client per 15 minutes.

Set `enabled: false` under `statistics:` in `/etc/rukebox/rukebox.yaml` to turn the whole
thing off; the radio behaves exactly as before.

### Monitoring settings

```ini
# How often to check the speaker is still connected (0 = disabled).
# With MUSIC_START_MODE=bluetooth, this same check is what starts the
# music, so a 0 falls back to the default there instead of disabling it.
SPEAKER_WATCH_INTERVAL_SEC=10

# Admin access point interface, and how often to look at who is on it
AP_INTERFACE=uap0
AP_WATCH_INTERVAL_SEC=60
```

Reading the access point's client list uses `iw dev uap0 station dump`,
which normally needs `CAP_NET_ADMIN`; `scripts/install.sh` installs a
narrow passwordless sudo entry for exactly that read-only command. **If
you change `AP_INTERFACE`, update the matching line in
`/etc/sudoers.d/rukebox-poweroff`**, otherwise access point usage simply
won't be recorded (everything else keeps working, and a warning is
logged once).

## Playback errors

An unreadable, corrupt or deleted file **never stops the radio**: the
failure is logged, recorded in the statistics with mpv's error message,
and playback moves straight on to the next track.

If failures keep coming — a whole folder of broken files, or an unmounted
drive — skipping through the entire library at full speed would hammer
the SD card and the CPU of a Pi Zero for nothing. So after a number of
**consecutive** failures, the next attempt is postponed:

```ini
# Pause after this many consecutive unreadable tracks
PLAYBACK_ERROR_MAX_RETRIES=5
# ...and wait this long before trying again
PLAYBACK_ERROR_BACKOFF_SEC=10
```

Both are adjustable **from the web interface** (Playback card), and each can be switched off on its own:

| Setting | Effect |
| --- | --- |
| `PLAYBACK_ERROR_MAX_RETRIES=0` | Feature off entirely. Failures are still recorded one by one, but never grouped into a pause. |
| `PLAYBACK_ERROR_BACKOFF_SEC=0` | The run of failures is still **recorded** (so the Stats pages show it) but playback retries immediately, with no waiting. |

A single file that plays resets the counter. The suspension itself is
recorded as a `playback_stalled` event, so the Stats pages show it
rather than leaving you wondering why the music went quiet.

## Backing up the configuration

The **Configuration** card (System tab) downloads every setting, the
custom announcements, the saved announcement track orders, the schedules,
the music lists, the liked tracks and the tracks set aside by the duplicate
check as one JSON file, and imports one back. A file exported before the last
four existed leaves them as they are. That is what makes a fresh installation cheap:
flash the card, install, import the file, done — no need to remember how
the last one was configured.

- **The web interface password and its session key are NOT in the file.**
  A downloaded file is one that gets copied to a USB stick and mailed
  around; the restored installation starts with no password, exactly like
  a fresh one, and the Security card sets a new one.
- **Settings are written line by line**, so every comment in
  `rukebox.yaml` survives an import, and only the values that actually
  differ are rewritten — importing the same file twice changes nothing,
  which the summary says out loud ("0 settings changed").
- **An import applies at once**: the daemon reads the settings again,
  and the schedules, the lists and the tracks set aside follow.
- **During provisioning**: drop the exported file on the SD card's boot
  partition (the FAT one) as `rukebox-config.json`, next to
  `firstrun.sh`. The first boot applies it before `install.sh` has ever
  run, so the values from the file are the ones that win. Nothing to
  rename, unzip or convert — the export *is* the file to drop.
- The same two operations exist on the command line, which is also how
  the provisioning path calls them:
  `python3 src/config_bundle.py export|import <file>`.

**Full backup** (same card, folded): a `.zip` with the configuration and,
only if you tick them, the statistics, the suggestions (names, devices,
votes) and the announcements' sound files - each with its size. Restoring
first says what the file holds, then applies it once confirmed: settings
merged as above, statistics and suggestions replaced, sounds added to
their announcements (the player restarts when the statistics were
restored).

**Pairings never travel with a configuration or a backup**: the speaker's and
the Flic button's pairings live on the card itself. A restored configuration
keeps the speaker's address, so the page says "Not paired" and "To finish"
asks to pair it again; the export also counts the Flic buttons that
were paired, and "To finish" asks for those until one is paired again.

The card setup page takes that `.zip` too, at its "radio" step: it reads
the configuration out of it and puts the announcements' sounds on the card,
in the folders they come from (only those under `/home/pi/audio`). Its
"files" step then lists every announcement of that configuration, each with
its own row to add sounds to, and says how many came from the backup. The
statistics and the suggestions are not written to the card - restore the
same file from the interface once the Rukebox runs.

**Storage health** (System health card): each storage - the microSD card,
or a USB key / disk holding the music - with its kind, free room,
read-only state (the kernel stops writing to a failing card), file system
errors and read/write errors since the start. A problem also shows in
"To finish".

## Updating the Pi

Two ways, both optional and independent. Whichever you use, **music,
settings and statistics are preserved** — only the program is replaced.

### The system packages (apt) are not updatable from the web

Deliberately, and the Update card says so instead of offering a button.
A full `apt full-upgrade` needs Internet access (which this Pi only has
when its personal Wi-Fi is up), takes several minutes on a Pi Zero, and
can restart NetworkManager — which drops the personal Wi-Fi and, with it,
the very page asking for it. On a machine with no screen and no keyboard,
an update that cuts its own way in is not a feature.

The card does show what the **local package cache** knows
(`apt list --upgradable`), and whether the Pi currently has Internet
access, so you can see when there is something to do. Then do it over
SSH, or over the USB cable:

```bash
sudo apt update && sudo apt full-upgrade
```

### Pushed from your computer (over USB, or over the network)

The normal path, and the one that matches the rest of this project: the
Pi needs nothing but the SSH access set up at provisioning time — no
internet, no Git, no package manager.

"Over USB" is only the default, not a requirement. The script talks to
the Pi over SSH, so any address the Pi answers on works: the USB cable
(`169.254.7.7`, tried first), `rukebox.local` over the admin access
point or your own Wi-Fi (tried automatically if the USB address does
not answer), or an explicit `--host`.

From the computer holding this project:

```bash
./bootstrap/push_update.sh            # macOS / Linux
```

```
bootstrap\push_update.cmd             # Windows (double-click, or from cmd)
```

It packs the project, sends it over, and runs the updater on the Pi.
The automatic retry against `rukebox.local` only happens when no
`--host` was given: naming a host explicitly means you already know
where the Pi is, and quietly trying somewhere else would be surprising
rather than helpful. Useful options, identical in both scripts:

```bash
./bootstrap/push_update.sh --dry-run          # show what would be sent
./bootstrap/push_update.sh --host rukebox.local
./bootstrap/push_update.sh --identity ~/.ssh/id_ed25519
./bootstrap/push_update.sh --no-restart       # install without restarting
./bootstrap/push_update.sh --keep-playing     # leave the music on while sending
```

If the Pi already runs the exact same version, it says so and stops
without touching anything.

**The push also tells the Pi where the tree comes from.** It sends what `git
describe` says of it - `v1.2.0-5-g5622dcf`, five commits past the v1.2.0 tag -
and the Pi records that as its version. Without it a pushed tree has no release
at all, and the Update card offered the last published release as "newer" to a
Pi that already had more than it (the push said "already runs this exact
version" while the card offered v1.2.0: both were reading a different
question). A tree pushed with uncommitted changes is stamped `-dirty`, which is
worth knowing before comparing it to a release.

**The music is paused for the transfer, on purpose, and comes back when the
services restart at the end of the update** (it is not resumed in between:
that only bought two seconds of music before the daemon went down). The
Bluetooth stream shares the air with the Wi-Fi the push arrives on, and
while it plays large packets get lost: measured on a Pi Zero 2 W, the archive
uploaded at about **3 KB/s** with the music playing and about **590 KB/s**
with it paused, which is the difference between a six-minute push and a few
seconds. The rate takes 10-30 s to climb back once the audio stops, so the
script times a small probe upload and waits rather than firing the archive
into a link that is still at 1 Mbit/s, and it retries once if the transfer
is cut. Only a pause the script asked for is undone, so a pause of your own
survives (and a push with `--no-restart` does resume the music at the end,
since nothing else will). `--keep-playing` skips the pause when you would
rather listen than wait.

**A page open while an update runs says so instead of looking dead.** The
updater writes a flag (`/var/lib/rukebox/updating`) for as long as it runs, the
status carries it, and the page shows "The Rukebox is being updated… — it comes
back on its own in a few seconds; this page reloads itself when it does", the
same full-screen notice as when the Pi is switched off. That covers every way
of updating from a terminal, not just the push. When the flag goes away the
page reloads by itself, so it is never left running a mix of old cached files
and new ones. A flag left behind by an updater that was killed is ignored and
cleared after fifteen minutes.

### From a GitHub release (needs network)

The **Update** card (System tab, detailed view) checks the latest
release published on GitHub - `Arubinu/Rukebox` by default, or your
fork: change the **GitHub repository** field (`owner/name`, setting
`updates: github_repo`). **Check for a new version** asks GitHub (the Pi
needs Internet for a moment: home Wi-Fi, or the USB cable's second card)
and says whether a newer release exists; **Install vX** downloads that
release's source archive over HTTPS and installs it exactly like a USB
update (validation first, backup, automatic rollback on failure,
settings, music and statistics kept). The installed release's tag then
shows beside the version.

Installing from the page needs **Allow updates from the web**
(`UPDATE_ALLOW_WEB`, the switch at the bottom of the Update card), like
the Git button below - same reasons. From SSH it needs nothing:

```bash
sudo rukebox-update --from-release          # the latest release
sudo rukebox-update --from-release v1.2.0   # a given tag
```

### Publishing a release

To publish one: push, then on GitHub **Releases > Draft a new release**,
pick or create a tag (e.g. `v1.0.0`) and publish. Only a *published*
release counts (not a draft, and a plain tag without a release is not
seen).

Build the setup page **from the tagged commit** and attach it to the
release — that is the file people download, and the version an
installation from it will record:

```bash
git tag -a v1.0.0 -m "v1.0.0" && git push --tags
git checkout v1.0.0
python3 bootstrap/build_setup_page.py
# dist/rukebox-setup.html: 2.0 MB (project archive 1.0 MB, release v1.0.0)
# installed tree hash: 571ca29903b5281ad636aa81e436ca6491361c74b1f963487db4bb59abf5dd49
# release badge: https://img.shields.io/badge/tree--hash-571ca29903b5-blue
```

Then **Releases > Draft a new release**, tag `v1.0.0`, attach
`dist/rukebox-setup.html`, and in the **description**:

- paste the `release badge:` line in the **description** — or just
  `tree-hash:` followed by the 12 characters it printed. The release
  *title* is plain text, a badge there would not render;
- its 12 characters are the same short hash the Rukebox shows in its Update
  card, so the two can be compared by eye;
- the hash only depends on the files, so re-running the build prints the
  same value: nothing is lost if the terminal is gone. It cannot be
  computed outside the build, because the page ships a subset of the
  repository (no `bootstrap/`, no docs).

Built away from a tag, the page still works but stamps the commit
(`fdeb1a1-dirty`), and the Pi will report that a newer release exists even
though it installed the same code. `--release v1.0.0` forces the value.

### From a Git repository (needs network, optional)

Only useful once the Pi can reach the internet — see *Personal Wi-Fi*
below, which connects it to your own network without dropping the admin
access point. Configure the repository once:

```ini
UPDATE_GIT_URL=https://github.com/you/your-repo.git
UPDATE_GIT_BRANCH=main
```

Then, on the Pi:

```bash
sudo rukebox-update --from-git
```

A button in the web interface does the same thing, but it only appears
if you explicitly opt in, with the **Allow updates from the web** switch
in the Update card (or `UPDATE_ALLOW_WEB=true` in `rukebox.yaml`). It is
saved as soon as you flip it, and takes effect at once.

**It is off by default for a reason.** The web interface has no
password by default (see *Security* above), so with it on, anyone connected
to the Pi's access point can trigger a fetch-and-run as root. The USB
path and SSH need none of this. If you never want web-triggered updates,
you can also delete the three `rukebox-update` lines from
`config/sudoers-rukebox`.

The web interface can only start an update from the URL **already in the
config file** — it cannot pass a repository of its own choosing, and the
updater it invokes lives in `/usr/local/sbin` (root-owned), not in
`/opt/rukebox` (owned by `pi`).

### What the updater actually does

In order, and stopping at the first problem:

1. **Validates before touching anything.** The new Python must compile
   and the shell scripts must parse, or nothing is changed at all. On a
   headless machine that powers itself off every day, a syntax error
   would otherwise mean a silent radio and an SD card to pull out.
2. **Compares versions** and stops early if they match.
3. **Backs up** the current `/opt/rukebox`.
4. Stops only the services that were actually running, swaps the files,
   and restarts exactly that set.
5. **Merges new settings** into `/etc/rukebox/rukebox.yaml` — settings
   introduced by the new version are appended with their documentation,
   and **no existing value is ever modified**.
6. Refreshes the systemd units and the sudo grants (the latter validated
   with `visudo` first, since a malformed sudoers file can lock `sudo`
   out of the machine).
7. Adds any missing confirmation sounds, without overwriting ones you
   replaced with your own.
8. Records the new version.

If something fails partway through, it **restores the previous version
by itself** and restarts the services, leaving the failed attempt in
`/opt/rukebox.failed` for inspection.

### Rolling back

```bash
sudo rukebox-update --list-backups
sudo rukebox-update --rollback          # restores the most recent backup
```

`UPDATE_BACKUP_KEEP` (3 by default) sets how many are kept.

### Which version is installed?

The web interface shows it in the **Update** card. From a shell:

```bash
cat /var/lib/rukebox/version.json
```

There is no version number to maintain by hand: the version is a digest
of the installed source files. It always exists, it works for a tree
pushed over USB with uncommitted changes, and it answers the only
question that matters — *is what runs on the Pi the same as what I have
here?* When the source came from Git, the commit and branch are recorded
alongside it.

An installation from `rukebox-setup.html` also records the release the
**page was built from**: `build_setup_page.py` stamps the tag it finds
(`git describe --tags`, or `--release v1.0.0`) into the copy it writes,
and `install.sh` records it. That is what the Update card compares against
the latest published release ("a newer version exists"), and what it shows
as the installed version. A page built away from a tag stamps the commit
instead (`fdeb1a1`, `-dirty` when the tree had uncommitted changes), which
is honest: that build is not the release.

A release can also **declare the tree hash it installs**, as a line in its
notes (`tree-hash: 26f2eae2`) or as a badge carrying it
(`![](https://img.shields.io/badge/tree--hash-26f2eae2-blue)`, the value
`build_setup_page.py` prints). When the Update card checks GitHub, a Pi
whose `tree_hash` starts with that value records the release's tag as its
version, and stops offering it: that is how an installation made from the
card, pushed over USB or done by hand gets named exactly, with no version
number travelling in the code. The tag is remembered in `version.json`, so
this comparison happens once — later checks are a plain tag comparison.

## Personal Wi-Fi (SSH access alongside the access point)

The access point (`setup_ap.sh`) and a client connection to your
personal network can run **at the same time**, thanks to the
**concurrent AP+STA mode** supported by the Pi's integrated Wi-Fi chip
(Broadcom, `brcmfmac` driver — the case for every model with integrated
Wi-Fi). The principle: a virtual interface `uap0` is created
specifically for the access point, leaving `wlan0` entirely free to
connect normally to your personal network.

Concrete result: the web interface stays **always** reachable via the
access point, and you can additionally toggle a connection to your
personal network on demand for SSH access — without ever losing access
to either one.

**Honest limitation**: this mode depends on the Wi-Fi driver and can,
on some models (notably the original Pi Zero W, with an older chip),
result in reduced throughput under concurrent use. For this project
(light web admin + occasional SSH), that remains plenty in practice.

### Setup

Important order: the `uap0` interface must exist **before** the access
point is created.

```bash
# 1. Create the virtual AP interface (at boot, via systemd)
sudo systemctl enable --now create-uap0.service

# 2. Create the access point ON uap0 (no longer on wlan0)
sudo /opt/rukebox/scripts/setup_ap.sh Rukebox-Admin "a-strong-password"

# 3. Connect the Pi to your personal network ON wlan0, in parallel
sudo /opt/rukebox/scripts/setup_home_wifi.sh
```

`setup_home_wifi.sh` asks for your personal network's SSID and
password, creates a NetworkManager profile with automatic reconnection
whenever the Pi is in range, and offers to write the created profile's
name to `/etc/rukebox/rukebox.yaml` (`home_wifi_connection` under `network:`) — that's what
enables the "Personal Wi-Fi" card in the web interface.

### Usage

Once configured, a card appears in the web interface with two
switches. **Connection active**: the Pi joins (or stays on) your personal
network and shows its IP address there (to SSH into it); off, the
connection is cleanly cut (`nmcli connection down`), without ever
touching the `uap0` access point. **Connect at startup** is the profile's
`connection.autoconnect`: off, the personal Wi-Fi stays off after every
restart until it is switched on again.

**The connection is kept, not just asked for** (`home-wifi-connect.service`,
`scripts/home-wifi-connect.sh`). NetworkManager does **not** come back from
a connection that failed for missing secrets: one lost packet during the
WPA handshake makes it ask for the key again, and a headless Pi has no
agent to answer — it then stays down until someone brings it up by hand.
That is not hypothetical: it happened here, and the Pi spent twenty
minutes off its own network. This service checks every few seconds that
the connection is up and takes it back when it is not, exactly as
`bt-connect.service` does for the Bluetooth speaker.

- It only acts when **both** switches agree: *Connection active* (the
  intent, remembered in `home_wifi_enabled`) and *Connect at startup*
  (the profile's `autoconnect`). Switching either one off is therefore
  never undone behind your back.
- When the page you are reading **arrived through that network**, the card
  says so and asks for confirmation before switching it off — otherwise
  the click closes the very page you clicked it from. Reached through the
  access point instead, no question is asked.
- The service is listed in System > System health with the others, and
  can be restarted there.

If you prefer the command line over the web interface:

```bash
# On the Pi, or via the access point
nmcli connection up "<profile name>"     # enable
nmcli connection down "<profile name>"   # disable
nmcli device show wlan0 | grep IP4.ADDRESS   # find the IP for SSH
```

### If the personal Wi-Fi keeps dropping: the network diagnostic

**System > System health > Network diagnostic** (or, over SSH,
`/opt/rukebox/scripts/network-check.sh`) reads the link to your access point
- signal, rates, frames lost on ten small pings and ten large ones - and
every time the link dropped since the Pi started, with who ended it. Each
drop is set against what the Bluetooth radio was doing at that second.

That last part is what it was written for. The Pi Zero's built-in chip is
**one radio for Wi-Fi and Bluetooth**. When the speaker goes through that
chip and is switched off, every attempt to reach it takes the antenna for
five seconds, and now and then the Wi-Fi loses its access point for it: the
link drops and comes back ten seconds later (the report then reads *"n of
the n drops came within 3s of a Bluetooth connection attempt giving up"*).
`bt-connect.sh` therefore behaves differently on that chip: its attempts
are half as long, and once the speaker has not answered for a couple of
minutes it tries every five minutes instead of every minute. A speaker
switched on later still connects at once by itself - the Pi stays
connectable - so the slower pace only matters for a speaker that never
calls back. On a USB Bluetooth adapter nothing changes.

### Limitation of the hostapd/dnsmasq path

If `setup_ap.sh` detected the absence of NetworkManager and used the
classic hostapd/dnsmasq path, the personal Wi-Fi toggle described above
(and the matching web interface card) doesn't work as-is: you'd need to
configure `wpa_supplicant` manually on `wlan0` in parallel, which is
outside the scope of this project. If this feature matters to you, a
recent Raspberry Pi OS image with NetworkManager (the default since
Bookworm) is the simplest path.

## Audio folders

Every folder below is scanned recursively (useful if you have more than
1000 tracks organized by artist/album), and each is a plain setting in
`rukebox.yaml` you can point anywhere you like:

| Folder | Default path | Setting |
| --- | --- | --- |
| Music | `/home/pi/audio/music` | `music` (`folders:`) |
| Button sounds (single click) | `/home/pi/audio/memes` | `memes` (`folders:`) |
| Cutoff announcement | `/home/pi/audio/cutoff_announcements` | `cutoff_announcements` (`folders:`) |
| Morning / double-click announcements | `/home/pi/audio/morning_announcements`, `/home/pi/audio/doubleclick_announcements` | the announcement's own folder (Custom Announcements card) |
| System sounds (clock, Wi-Fi, keep-alive) | `/home/pi/audio/system` (your replacements in `system/custom`) | System sounds card |

The music list is cached (`MUSIC_CACHE_FILE`) to avoid rescanning the SD
card on every startup; after adding new tracks, force a rescan:

```bash
echo '{"cmd":"rescan_music"}' | nc -U /tmp/rukebox_control.sock
```

(The web interface's "Rescan music" button does the same thing.)

### Putting music on the Pi

The **Add music** card (Home) copies tracks over the Wi-Fi and
sends only what is missing. It asks the Pi what it already has, compares
that with what you selected, and uploads the difference — so syncing the
same folder twice sends nothing the second time, which matters when the
destination is an SD card on a Pi Zero.

- **Choose a folder…** keeps the sub-folders (artist, album) but not the
  chosen folder itself: its *contents* land in the music folder, like
  `rsync source/ destination/`. This is the desktop-browser path.
- **Choose files…** works everywhere, a phone included, where browsers
  cannot pick a folder at all. The files land at the root of the library.
- Files are compared on size and date the way `rsync` compares them, and
  each copy keeps the date of the file it came from — which is what makes
  the second sync recognise everything as already there.
- The card says what it is about to send and how much room is left on the
  Pi *before* anything moves, and the track list is rescanned at the end.
- **Lyrics and covers travel with the tracks**: `.lrc`, `.srt`, `.vtt` and
  `.txt` files, and `.jpg` / `.png` / `.webp` pictures. Everything else a
  folder can contain (`desktop.ini`, `.DS_Store`, playlists, hidden
  files...) stays on your computer.

The same thing by hand, over SSH or the USB cable:

```bash
rsync -av --info=progress2 ~/Music/ pi@rukebox.local:/home/pi/audio/music/
```

### Your own lists: everything, or only some of it

The **Lists** card (Home) decides what the radio plays. Left alone, it plays
everything in the music folder, in the order `music_order_mode` gives. A
list replaces that with a smaller set:

- **A manual list** holds the songs you add to it, in the order you added
  them: the **+♥** button beside a song of the Library card offers the lists,
  and the list's own page shows what it holds, with a cross to take a song
  out. With `music_order_mode: ordered`, a manual list plays in the order you
  built it (`random` and `random_albums` shuffle it like the library).
- **A genre list** holds everything the library tags with the genres you
  tick - several genres at once if you like. It follows the library by
  itself: a song added tomorrow with that genre is in the list tomorrow,
  without touching anything. The genres come from the files' own tags (the
  same ones the Library card's genre filter lists), so a genre list only
  holds songs whose tags have been read - the Library card says how far that
  has got ("reading the tags: n / total"). Until then, those songs are simply
  not in it yet.
  A tag often holds several genres at once (`Alternative Metal;Kawaii
  Metal`, also written with commas or slashes): each of them then counts on
  its own - in the list, and in the Library card's genre filter too - so
  that song belongs to both.
  Genres are **searched, not scrolled**: type a few letters in the box, tick
  what you want, and what is ticked stays above as pills you can take back.
  Eight genres are offered at a time - the search narrows them - and the
  number beside each one is how many tracks carry it.
- **Opening a list** (a tap on its row) shows it on its own, in place of the
  list of lists: its songs, and **Play / Edit / Delete**. A **←** brings the
  lists back. Both kinds show what they hold - read only for a genre list,
  since those songs are the library's - and a very long one stops at 200 rows
  and says how many are left out. Nothing has a scroll of its own: the page
  scrolls, so a finger never ends up moving the wrong thing.
- **Play** on a list makes it the list being played and starts it at once
  (the song playing fades out into it). **Play everything**, at the top of
  the card, goes back to the whole library. The choice is kept in the
  daemon's state, so a reboot comes back to the same list.

The Library card's genre filter has one shortcut: with a genre chosen, **Play
this genre** makes the list for it (or reuses the one that already matches)
and plays it - the quickest way to hear one kind of music for a while. Songs
are added to a manual list the same way from anywhere the Library shows them.

The lists are one file, `/etc/rukebox/music_lists.json`
(`music_lists_file`), plain JSON written by the interface - it travels with
the rest in a configuration backup.

### Getting more volume out of a quiet speaker

`audio_compression` (`playback:`) is `off` by default: the sound is exactly
what the files contain. The other two values apply a loudness filter in mpv,
before the volume control - `soft` or `strong`:

- the quiet passages come up (up to 5 dB for `soft`, 9 dB for `strong`),
- the peaks are tamed and the result is held just below full scale, so
  nothing clips,
- loud tracks end up a touch quieter and less dynamic - that is what evening
  the loudness out means, and it is what makes a quiet recording audible on
  a small speaker.

Nothing is written to the files, and the volume slider keeps working exactly
as before: this is not a higher ceiling, it is a different balance. It
applies to music, announcements and the system sounds alike, and takes
effect without a restart. Choose **Volume boost** in Settings > Volume, or
by hand:

```bash
sudo python3 src/config_file.py set AUDIO_COMPRESSION=soft
echo '{"cmd":"reload_config"}' | nc -U /tmp/rukebox_control.sock
```

If the mpv on the Pi was built without those filters, the daemon says so in
the log (`journalctl -u rukebox-daemon`) and plays without them rather than
staying silent.

### Sound profile, crossfade and the silence at the end

- **Sound profile** (Settings > Volume, `playback.audio_equalizer`): `bass`
  (more low end, for a small speaker), `voice` (clearer voices, less rumble),
  `bright` (more treble) or `night` (soft bass and tamed peaks, for listening
  quietly). It is applied before the volume boost, so the boost's limiter
  still keeps the boosted bands from clipping. Applied without a restart.
- **Crossfade** (Settings > Playback, `fades.crossfade_sec`, 0 = off, 10 at
  most): the end of a song fades out under the start of the next. A second
  player is prepared, paused, eight seconds before the end - starting one
  takes a second or two on a Pi Zero - and the fade only happens if it is
  ready in time; otherwise the song simply plays out. No crossfade before a
  spoken introduction or a dedication, nor when the cutoff, a planned
  restart or the end of a non-looping list comes next.
- **Skip the silence at the end** (Settings > Playback,
  `playback.skip_trailing_silence`, on by default): many files end on two to
  four seconds of nothing. The library looks at the last 30 seconds of each
  file once, in the background (about 1.5 s a file on a Pi Zero), and the
  radio moves on as soon as only silence is left. A silence in the middle of
  a song - before a hidden track - is not an ending, so it is played.

### One volume instead of two: the speaker's own

The radio has a volume of its own (the slider, and what an announcement or a
System sound asks for), and a Bluetooth speaker has a second one - the one its
own buttons move. By default the two add up: turn the speaker down and the
slider has that much less to work with.

`speaker_volume_link` (`playback:`) makes them one volume. The slider then sets
the speaker's own volume, the radio sends it everything it has, and a press on
the speaker's volume buttons is read back within a couple of seconds, so the
slider and the speaker can never drift apart. This is also the case where a
volume set on the speaker is what `volume_mode: session` keeps.

Turned off (the default) nothing changes: the slider is the radio's software
volume, and the speaker's is a second one on top of it - which is what you want
when the radio feeds something that has no volume buttons at all.

Two things worth knowing:

- the volume after a restart is `base_volume` as before, and the speaker is set
  to it: the radio hands its volume to the speaker when it starts, again when
  the speaker connects, and again when the music starts (a speaker that is not
  playing anything yet ignores the volume it is sent);
- `volume_change: fade` glides the radio's own volume only. A linked change goes
  to the speaker at once, because a fade would be twenty AVRCP round trips a
  second.

Choose **The speaker's volume is the volume** in Settings > Volume, or by
hand:

```bash
sudo python3 src/config_file.py set SPEAKER_VOLUME_LINK=true
echo '{"cmd":"reload_config"}' | nc -U /tmp/rukebox_control.sock
```

**The speaker's volume buttons are locked** (Playback > Volume,
`speaker_volume_lock`) takes them away from whoever stands next to the
speaker. The Pi cannot stop a speaker from changing its own volume, but it
reads it every two seconds and puts it back: linked, to the interface's volume
(the slider keeps the say); not linked, to the level the speaker had when it
connected. A press is therefore heard for a moment before it is undone. It
needs a speaker that shares its volume over Bluetooth (AVRCP absolute volume):
one that keeps it to itself cannot be held. The speaker's other buttons
(play/pause, next, previous) are locked by setting their action to
**Nothing** under Buttons.

### An announcement that plays only some of the time

Each announcement has two settings, **When it starts on its own** and
**When started by hand** (the Announcement button, or a click that plays
it): every time, or 3 out of 4, 2 out of 3, 1 out of 2... down to 1 out of
10. It is exact, not a coin toss: every series of 2 holds exactly one play
for "1 out of 2", in a random order, so it can never turn into 1 out of 10
by bad luck. A skipped daily play still counts for the day. The "Play"
button in its own row is a test and always plays.

### Restarting after the song, the system sounds, the announcement files

- **Restart after this song** (System card): the service restarts once the
  song playing ends - after the **Restart sound** (System sounds card). The
  same button cancels it while it waits. **When nothing is playing** (no
  music, or the music is paused) there is no song to wait for: it restarts at
  once, and the button says so, reading **Restart now** with the restart
  symbol instead of the waiting clock.
- **System sounds**: each can be listened to, replaced by your own file,
  put back to the original, or **turned off** - the keep-alive sound
  included, for a speaker that stays awake by itself (no energy spent
  looping a near-silence).
- **Announcement files**: tick several files to move them up or down
  together, or delete them at once.

### Adding your own announcement types

The button sounds and the cutoff are built in - the cutoff is tied to
shutting the Pi down. Beyond those, the **Custom Announcements** card in
the web interface lets you
add as many additional, independent announcement types as you like, each
with:

- a **name**,
- a **folder** (anywhere; created on the Pi like any other audio folder,
  ONE of its files played per trigger, in turn, according to
  `ANNOUNCE_ORDER_MODE` - the cutoff too: one file, then the Pi switches
  off - same as every other announcement folder — see "Announcement track order"
  below). The **Browse…** button beside that field walks the Pi's own
  directories instead of asking you to type a path on a phone keyboard:
  directories only, each one showing how many audio files it holds
  directly, and confined to `/home/pi`, `/media`, `/mnt` and `/srv` so
  the picker can never become a way to browse the machine,
- **what starts it** (the music fades out first and resumes afterward):
  - every day at a **fixed time**;
  - a **delay after the music starts**, or **after the Pi starts** - once,
    a set number of times, or indefinitely (the delay then separates two
    plays). Delays already past when the radio (re)starts are not
    replayed, and several missed while something else was playing are
    covered by a single play;
  - **only by hand** (the Announcement button, a click, "Play").

  The "k times out of n" setting applies to every automatic start.
- **what follows it** (**Then**), when it started on its own: back to the
  music (the default), or pause, mute, loop the track or the album, normal
  playback, volume up or down, the sleep timer, standby, or switching the Pi
  off. A play started by hand - the Announcement button, a click, "Play" -
  never does it.

Each one also gets a **"Play now"** button, to test it immediately
without waiting for (or consuming) its scheduled time - useful right
after dropping files into a brand new folder. A **Disable** toggle keeps
a type defined without it firing, and **Delete** removes it entirely.

Like the settings, **changes here apply immediately** - add, edit,
disable or delete a custom announcement type and it takes effect on the
very next scheduler tick (within 15s), no `systemctl restart` needed. They are stored separately from
`rukebox.yaml`, in `/etc/rukebox/announcements.json` (`ANNOUNCEMENTS_FILE`)
- plain JSON rather than YAML, since this is a list managed entirely
through the web form rather than something meant for hand-editing.


### Putting the sounds in (Announcement files card)

Everything about an announcement's content happens in one card,
**Announcement files** (Settings tab), with no path to know:

1. **Create the announcement** in Custom Announcements, and simply leave
   the **Folder** field empty: a folder is created for it
   (`/home/pi/audio/announcements/<name>` with the default paths). The
   interface then takes you straight to its files.
2. **Add files…** sends the sounds you pick from the phone or computer
   (MP3, Opus, OGG, WAV, M4A, FLAC), one after the other, with the
   progress shown. A file of the same name is replaced.
3. Each sound has a **trash can** to remove it from the Pi (after a
   confirmation), and the arrows to arrange them, which only matters with
   the fixed announcement order.

The same card works for the built-in folders too (button sounds and
cutoff), and every custom announcement has a
**Files** button leading to it. The server always decides the folder
itself from the announcement chosen - the request only ever names a file
- keeps only bare audio file names, and refuses folders outside
`/home/pi`, `/media`, `/mnt` and `/srv`.

### Announcement track order

`ANNOUNCE_ORDER_MODE` (`playback.announce_order_mode` in `rukebox.yaml`,
default `ordered`) applies to every announcement folder — morning,
cutoff, double-click, and any custom type — the same three choices as
the music order mode (see "How it works" above): `random`,
`random_albums`, or `ordered` (natural filename sort).

In `ordered` mode specifically, the **Announcement files** card
in the web interface lets you pick an exact sequence per folder instead
of relying on filenames: select the folder, reorder its files with the
↑/↓ arrows, then **Save order**. A file added to the folder later, not
part of any saved order yet, is appended after the saved ones rather
than skipped. **Reset to alphabetical** clears the saved order for that
folder, reverting to natural sort. Changes apply immediately, no
service restart. Saved orders live in `/etc/rukebox/track_order.json`
(`TRACK_ORDER_FILE`) — plain JSON, like `announcements.json`, not part
of the hand-editable YAML settings.

This reordering UI is deliberately **not offered for the music library
itself** — practical for the handful of clips an announcement folder
typically has, not for a collection of hundreds or thousands of tracks;
`ordered` mode for music is natural filename sort only (name files
"01 - ...", "02 - ..." to control it).

### A volume for each announcement

Each announcement source has **its own volume, on two lines**: a switch,
then the volume as a number field — the same shape as *Base volume* in the
settings. They sit under the announcement picker of the Announcement files
card, in the form of a custom announcement, and in each row of the System
sounds card.

- **Off** (the default) is what the radio always did: the announcement
  plays at the volume of the music, and follows the Home slider like the
  songs do. The field is greyed while off, and the value is kept for the
  day it is switched back on.
- **On** makes that one announcement play at its own volume, whatever the
  music is at: a cutoff announcement that stays quiet at night, a button
  sound that stays discreet, a morning announcement loud enough to wake
  someone up. The music takes its own volume back as soon as the
  announcement is over, and the Home slider keeps showing the music's
  volume the whole time — the two are separate, on purpose.
- It applies to every announcement source: the button sounds (`meme`),
  the cutoff (`cutoff`), each custom announcement, and each **System
  sound** (keep-alive, clock confirmation, Wi-Fi hotspot connection,
  restart) — whatever the picker shows.
- Stored in `/etc/rukebox/announcements.json`, next to the announcements
  themselves, under `volumes`. Plain JSON like the rest of that file, not
  a YAML setting; deleting an announcement drops its volume with it.
- Changing a volume applies at the **next** play of that announcement, no
  restart. Moving the Home slider *during* an announcement overrides it
  until that announcement ends, which is the one case where the slider
  and the announcement disagree.
- **Watch out for a source left far above the music**, which reads exactly
  like a broken radio: the jingle comes out loud and clear, then the song
  that follows is there but almost inaudible, and nothing in the page looks
  wrong. It happened for real — music at 19, a button sound at 100, about
  **40 dB** apart. The row now says so as soon as one of them reaches four
  times the music's volume ("Much louder than the music (19): the jingle
  will drown it out."), and the line follows the Home slider as you move it.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Standard library only. GitHub runs them on every push
(`.github/workflows/tests.yml`), together with the interface's own tests:
the real page loaded in jsdom against a fake Pi (`tests/web/`). Those need
Node.js on the computer you develop on - never on the Pi, which installs
nothing of it:

```bash
npm install
npm test
```

The web server's tests need Flask, so they are skipped on a computer without
it; run the whole suite on the Pi against the installed code:

```bash
scp -r tests pi@169.254.7.7:/tmp/rukebox-tests
ssh pi@169.254.7.7 'RUKEBOX_SRC=/opt/rukebox/src python3 -m unittest discover -s /tmp/rukebox-tests -v'
```

## Logs and troubleshooting

```bash
journalctl -u rukebox-daemon.service -f
journalctl -u rukebox-web.service -f
journalctl -u flicd.service -f
journalctl -u flic-bridge.service -f
journalctl -u bt-connect.service -f
```

The journal is kept on the card across restarts (Raspberry Pi OS keeps it in
memory only; `/etc/systemd/journald.conf.d/50-rukebox.conf` changes that,
capped at 48 MB), so what happened before the last power-off can still be
read: `journalctl -b -1 -u rukebox-daemon.service`, and `journalctl
--list-boots` for the earlier ones. To keep it small, rtkit's routine messages
and the page's successful reads of the API are not written.

> **If the Bluetooth search finds nothing** although devices are nearby: the
> radio is probably soft-blocked at the kernel level, which BlueZ cannot undo
> (`bluetoothctl power on` fails while the switch is blocked) — some images
> come up like that on a fresh install, and then every Bluetooth feature is
> silently dead. `rukebox-bt-radio.service` clears the switch at every boot
> (`journalctl -u rukebox-bt-radio.service`); the web page says so instead of
> "no device found". To look and to fix it by hand:
> `cat /sys/class/rfkill/rfkill*/name /sys/class/rfkill/rfkill*/soft` (`1` =
> blocked) then `sudo /opt/rukebox/scripts/bt_radio.sh`.

> **If a Bluetooth speaker refuses to connect** (`br-connection-profile-unavailable`):
> a headless install never has an *active* seat, and WirePlumber's Bluetooth
> monitor waits for one before registering the A2DP profiles, so BlueZ has
> nothing to offer. `wireplumber.conf.d/10-rukebox-bluez.conf` is installed for
> exactly that; `bluetoothctl show` must list **Audio Source**. A speaker that
> answers `br-connection-refused` instead is simply already connected elsewhere
> (a phone): disconnect it there, or turn that device's Bluetooth off.

> **If every connection fails with `br-connection-busy`, or a search answers
> `org.bluez.Error.InProgress` while nothing is scanning**: the Bluetooth
> radio's command queue is jammed, usually by a connection that was half
> established when the speaker went away. The kernel says so — `dmesg` shows
> `Bluetooth: hciN: command 0x041f tx timeout` every twenty seconds for ever,
> while `hciconfig` still reports `UP RUNNING` and the radio still answers
> `btmgmt`. **`bt-connect.service` repairs it by itself**: it stops
> `bluetooth`, takes the controller down and up, starts `bluetooth` again and
> reconnects — about forty seconds, once every five minutes at most
> (`journalctl -u bt-connect.service -f` shows `Repairing hciN: ...`, and the
> line just above says what the connection attempt answered).
>
> It repairs on either of two signs, and both are needed: a controller that
> **refuses** the attempt (`br-connection-busy`, `InProgress`) is jammed
> whatever the kernel says, and a controller whose kernels timeouts have
> **grown since the radio last worked** is jammed even if they have stopped
> coming. Only the *controller's* own timeouts count: a speaker that is simply
> switched off or out of range produces `link tx timeout` instead, and no
> repair is attempted for that — the attempt is answered
> `br-connection-refused` or "not available", and the script keeps trying
> quietly. If it never recovers, the speaker itself is holding a stale link:
> switch it off and on once.
>
> **If the speaker is connected, its volume is up, the radio says it is
> playing — and nothing comes out at all, whatever the volume**: the link is
> up but carries no audio. Nothing looks wrong: `bluetoothctl info` says
> connected, the BlueZ transport says `active`, the sink runs, `wpctl` shows
> the streams as active, and the speaker even reports its own volume at its
> maximum. The one honest witness is the **controller's own byte counter**
> (`hciconfig hciN` → `TX bytes`): it does not move by a single byte while
> audio plays. `bt-connect.service` watches exactly that — about fifteen
> seconds with nothing on the air while the daemon says it is playing, and it
> re-establishes the link by itself (`The speaker is connected but the radio
> is sending it nothing: repairing.`). Chasing the volume is a dead end in
> that state; only a fresh link clears it.

## Clock: hardware RTC module required (no Wi-Fi)

**Without Wi-Fi, the Raspberry Pi has no source to know the time.** It
doesn't have a battery-backed hardware clock by default (unlike a PC)
and can't sync via NTP since there's no network. Without a fix, the
time would only come from `fake-hwclock` (last time saved before
shutdown), which drifts with every power-on/power-off cycle —
unusable for schedules accurate to the minute.

**A hardware RTC module is therefore required**, not optional. The
simplest and most common: an I2C **DS3231** module (a few euros, CR2032
battery, typical accuracy on the order of a minute per year).

### Installing the DS3231 module

The two `config.txt` lines this needs are written for you, by
`scripts/install.sh` (and by `bootstrap/provision_sdcard.sh` on a card it
prepares, before the first boot). An installation that already declares
its own `dtparam=i2c_arm` or a different `i2c-rtc` overlay keeps it: only
the missing half is added. They take effect at the next boot.

```bash
# Wiring: VCC->3.3V/5V, GND->GND, SDA->GPIO2 (pin 3), SCL->GPIO3 (pin 5)

# Normally already done by install.sh - this is what it writes, and how
# to do it by hand if you are on an installation that predates it:
echo "dtparam=i2c_arm=on"     | sudo tee -a /boot/firmware/config.txt
echo "dtoverlay=i2c-rtc,ds3231" | sudo tee -a /boot/firmware/config.txt
sudo reboot

# Check that the module is detected (address 0x68 expected):
sudo i2cdetect -y 1

# And that the system reads the time from it ("RTC time:" must not say n/a):
timedatectl

# Disable fake-hwclock, which would conflict with the hardware RTC
sudo apt-get -y remove fake-hwclock
sudo systemctl disable fake-hwclock 2>/dev/null || true

# Check that the system correctly reads the time from the RTC at boot
sudo hwclock -r
```

### Initial time setting (once, required)

Since there will never be a network to provide the time automatically,
it needs to be set manually once before the RTC can keep it on its own:

```bash
# Set the system time (match it to the actual current time)
sudo date -s "2026-09-13 14:32:00"

# Write this time to the RTC module so it's kept even while powered off
sudo hwclock -w

# Check: should show the same time
sudo hwclock -r
```

From then on, the kernel automatically rereads the time from the RTC at
every boot (via the `i2c-rtc` overlay) with no intervention or network
needed. `rukebox_daemon.py` checks for the presence of `/dev/rtc0` at
startup and logs an explicit warning if the module isn't detected.

**Plan for over time**: even a good RTC drifts very slightly (typically
a few seconds per month). Since there's no Wi-Fi to auto-correct it,
plan to check the time occasionally (`hwclock -r`) and correct it
manually if needed, for example once a quarter.

### Giving the Pi the time from the phone you are holding

The Clock card has a **"Use this device's time"** button, and it is the
quickest route by far: the page sends the clock of whatever you are
reading it on, over the Wi-Fi you are already connected to, and the Pi
applies its own timezone to it. Nothing to pair, nothing to type, no MAC
address, no confirmation dialog. A phone keeps its own time right through
its own network, so this is as accurate as the phone is.

Two reasons it exists rather than just the Bluetooth fallback below:

- **It is the only route that works from an iPhone**, which never exposes
  its time over Bluetooth for reading.
- **The Bluetooth route asks you to confirm a pairing on the very phone
  whose screen is showing this page.** That is awkward by nature (the
  phone's confirmation appears over the browser), so it is kept for what
  it is good at - a device that is already paired and trusted, giving the
  time at startup with nobody there - and not for "I am standing here,
  the clock is wrong".

It is sent as UTC, explicitly, so the phone's timezone can never leak into
the Pi's clock; the Pi then shows the result ("the Pi's clock has been set
from this device").

### Fallback #2 (optional): time via Bluetooth

If you don't install an RTC right away, or as a backup, the daemon can
attempt to recover the time from a Bluetooth device **already paired
beforehand** (e.g. your phone), via the standard "Current Time Service".
This happens as a transient connection, with no re-pairing each time.

**Reliability not guaranteed**: many phones (iPhones in particular)
don't expose this service for reading, even once paired, for
manufacturer privacy reasons. Rather than discovering this at the first
real startup without an RTC, **test it right away** with the provided
interactive script:

```bash
sudo /opt/rukebox/scripts/setup_bt_clock.sh
```

This script scans nearby devices, lets you pick one, pairs it, then
**immediately tests** reading the time. On failure, it explains the
likely cause (service not exposed, often on iPhone) and offers to try
another device, or to cleanly give up — in which case
`BT_CLOCK_ENABLED=false` is automatically written to the config, and
only the grace period will be used. On success, `BT_CLOCK_MAC` and
`BT_CLOCK_ENABLED=true` are filled in for you.

### The timezone

Every time the Pi reports — the clock under the logo, the statistics, the
morning and cutoff triggers — is in **its own** timezone. That one is shown
in the Clock card, and can be changed
there: the field offers the system's own list of zones
(`timedatectl list-timezones`) and anything else is refused.

Two things are worth knowing, and they are why the card says so:

- **Setting the time by hand does not change the timezone.** The hour you
  type is read in the zone currently in force. If the clock is right but
  the hours look wrong, it is usually the timezone, not the time.
- **The Bluetooth fallback reads UTC** (the Current Time Service is
  defined that way) and converts it to the Pi's local time before setting
  anything.

### Confirmation sounds

Without a detected RTC, the daemon plays a short sound on the speaker
once the clock status is known, so you know without checking the logs
whether it worked:

- `CLOCK_OK_SOUND` (default `/home/pi/audio/system/clock_ok.wav`): two
  soft rising notes — the time was recovered via Bluetooth.
- `CLOCK_FALLBACK_SOUND` (default `/home/pi/audio/system/clock_fallback.wav`):
  two soft descending notes, similar but distinguishable timbre — the
  recovery failed (or wasn't enabled), the daemon falls back to the
  grace period with whatever time is available.

Both files are provided in `assets/sounds/` (synthesized, deliberately
discreet, non-strident) and copied automatically by `install.sh`.
Replace them with your own sounds if you prefer, keeping the same paths
or adjusting the config.

**With an RTC detected, no sound plays**: no ambiguity to resolve, the
clock is silently reliable from startup.

For these sounds to be audible, the speaker must be connected by the
time the clock status is decided. `bt-connect.service` is a supervisor
that runs for as long as the Pi does: it checks every 3 seconds, asks
BlueZ to connect when the speaker is not there (quickly at first, then
about once a minute), and stops doing anything but watching once the
connection is up. It also enables the adapter's *connectable* setting,
which BlueZ leaves off at every start — without it the Pi refuses every
incoming connection, so a speaker that remembers the Pi can never come
back on its own. The daemon then waits up to `SPEAKER_READY_TIMEOUT_SEC`
seconds for the speaker to be ready before playing the sound — past that
delay, the sound is simply skipped (no indefinite wait).

The Pi's audio output comes from PipeWire and WirePlumber, which run as
*user* services of `pi` — and WirePlumber is also what registers the
Bluetooth audio endpoints with BlueZ. `install.sh` therefore enables
lingering (`loginctl enable-linger pi`) so they start at boot on their
own: with no session there is no audio output at all, and BlueZ has no
A2DP endpoint to connect a speaker to.

### Wi-Fi hotspot connection sound

A short chime (`AP_CONNECT_SOUND`, default
`/home/pi/audio/system/ap_connect.wav`) plays once whenever a new
device joins the admin access point — handy confirmation that your
phone actually connected without having to check the web UI. It's a
short rising two-note ping, in the same discreet style as (and
distinguishable from) the clock confirmation sounds above, also
provided pre-synthesized in `assets/sounds/`. Set `AP_CONNECT_SOUND` to
an empty value to disable it.

This one is played through a disposable, independent mpv instance
rather than the one playing music, specifically so it never interrupts
whatever is currently playing — someone can join the hotspot at any
moment, including in the middle of a track.

### Startup grace period

The two fallbacks (RTC detection, then a Bluetooth attempt if needed)
run **in parallel with the rest of startup** (mpv launches and plays
music immediately, without waiting) — but the scheduler itself
evaluates no time-based trigger (morning announcement, cutoff) until a
reliable source has been confirmed. `CLOCK_SYNC_GRACE_SEC` sets the
upper bound on this wait: past this delay since launch, the scheduler
starts anyway with whatever time is available, so as never to block the
radio indefinitely if both fallbacks fail.

```bash
CLOCK_SYNC_GRACE_SEC=30   # seconds
```

Adjust based on how reliable your Bluetooth connection is (a higher
value gives the CTS attempt more time, at the cost of a scheduler that
starts later if both fallbacks fail).

## Known limitations

- `flic_click.py` assumes the SDK's Python module `fliclib.py` is
  reachable via `FLICLIB_PATH` (see the top of the file) — adjust the
  path if you install the SDK elsewhere.
- There's no universal Bluetooth command to remotely power off a
  speaker: we simply disconnect and rely on the speaker's auto-off. If
  your speaker doesn't have a reliable auto-off, a smart plug
  controlled by the Pi is a more robust alternative (not included in
  this project).
- The access point and a personal Wi-Fi run together on one radio
  (`uap0` beside `wlan0`), so both are on the same channel, and the
  Bluetooth shares that radio too (see "If the Bluetooth sound skips").
- The web interface has **no password by default** (see the Security
  section above): it assumes the local Wi-Fi access point is the
  protection perimeter, and the optional password is a second layer.
  Never expose it on a shared network.
- Settings saved from the web interface apply at once; the few read
  only at startup (listed in `config_schema.RESTART_REQUIRED`) say so
  and offer a restart of `rukebox-daemon`.
- Statistics recorded **before the clock is established** carry
  approximate timestamps. They're retro-corrected as soon as the time is
  known (and flagged in the interface until then), but if the time is
  *never* established during a boot, that session's timestamps stay
  approximate — they're shown in italics rather than presented as fact.
- Access point usage is only recorded if the client list can be read
  (`iw dev uap0 station dump`, see the Statistics section). With a
  customized `AP_INTERFACE` and no matching sudoers entry, that one
  metric stays at zero; nothing else is affected.
- Speaker drop detection polls `bluetoothctl` every
  `SPEAKER_WATCH_INTERVAL_SEC`, so a disconnection is noticed within
  that window rather than instantly. Lowering it too far costs CPU on a
  Pi Zero for little gain. That same window is the delay before
  `MUSIC_START_MODE=bluetooth` starts the music once the speaker is
  connected.
- The updater replaces `src/`, `scripts/`, `web/`, `config/` and
  `systemd/` wholesale. Anything you added by hand inside `/opt/rukebox` is lost on
  update (it is in the backup, but not restored automatically). Keep
  local changes in the project you push, not on the Pi.
- **In a container**, the network around it is not the private access point
  the security model assumes: the "Guest access" quotas, the portal and the
  device bans are all about that access point, so put a password on and mind
  who can reach the port. The stream is served to anyone who can reach it -
  the audio itself is not behind the password, only the button that offers
  the address is.
- **In a container**, "switch off" ends its processes and the restart policy
  brings them back: `restart: no` in the compose file is what makes the
  evening cutoff leave the container down until you start it.
- Updating from Git needs the Pi to have temporary network access. With
  no network it fails cleanly, changing nothing, and points at
  `setup_home_wifi.sh`.
- `UPDATE_ALLOW_WEB=true` lets an unauthenticated interface trigger code
  to be fetched and run as root. That is a deliberate, documented
  trade-off, off by default — see *Updating the Pi*.
