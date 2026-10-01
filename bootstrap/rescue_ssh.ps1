<#
.SYNOPSIS
  Puts an SSH public key on a Rukebox SD card, for the Pi that never let you in.

.DESCRIPTION
  The card's boot partition is the only way into a Pi whose account was not set
  up. This writes a one-shot firstrun.sh that, at the next boot, records what the
  account looks like (rukebox-account.log, available_keys, permissions, the
  account service state, cloud-init) into rescue.log on the card, installs the
  key in ~/.ssh/authorized_keys with the right owner and permissions, and puts
  /bin/bash back as the login shell.

  Power the Pi off, put the card in this computer, run this, put the card back.

.PARAMETER Path
  The card's boot partition. Detected from the drives when omitted.

.PARAMETER Key
  The public key to install. Defaults to ~/.ssh/id_ed25519.pub, then id_rsa.pub.

.EXAMPLE
  .\rescue_ssh.cmd
  .\rescue_ssh.ps1 -Path E:\ -Key C:\Users\me\.ssh\id_ed25519.pub
#>
param(
    [string]$Path = "",
    [string]$Key = ""
)

$ErrorActionPreference = "Stop"

function Find-BootPartitions {
    $found = @()
    foreach ($drive in Get-PSDrive -PSProvider FileSystem) {
        $root = $drive.Root
        if ((Test-Path "$root\config.txt") -and (Test-Path "$root\cmdline.txt")) { $found += $root }
    }
    return $found
}

# A drive letter on its own ("E", or "E:") is what people type.
function Normalize-Path([string]$p) {
    $p = $p.Trim()
    if ($p -match '^[A-Za-z]$') { $p = "$p`:" }
    elseif ($p -match '^[A-Za-z]:$') { $p = "$p\" }
    return $p
}

if (-not $Path) {
    # @(): a single candidate would come back as a string, and $candidates[0] its first letter.
    $candidates = @(Find-BootPartitions)
    if ($candidates.Count -eq 0) {
        Write-Host "No SD card boot partition found (a partition with config.txt and cmdline.txt)." -ForegroundColor Red
        Write-Host "Insert the card, then run this again, or pass -Path E:\"
        exit 1
    }
    if ($candidates.Count -eq 1) {
        $Path = $candidates[0]
        Write-Host "Boot partition detected: $Path"
    } else {
        Write-Host "Several boot partitions found:"
        for ($i = 0; $i -lt $candidates.Count; $i++) { Write-Host "  $($i + 1)) $($candidates[$i])" }
        $choice = Read-Host "Number to use"
        if ($choice -notmatch '^\d+$' -or [int]$choice -lt 1 -or [int]$choice -gt $candidates.Count) {
            Write-Host "Not a valid number." -ForegroundColor Red
            exit 1
        }
        $Path = $candidates[[int]$choice - 1]
    }
}
$Path = Normalize-Path $Path
if (-not (Test-Path "$Path\config.txt")) {
    Write-Host "$Path has no config.txt: that is not a boot partition." -ForegroundColor Red
    $names = (Get-ChildItem $Path -ErrorAction SilentlyContinue | Select-Object -First 12 -ExpandProperty Name) -join ", "
    if ($names) { Write-Host "It holds: $names" }
    Write-Host "The card's boot partition is the small FAT32 one (bootfs)." 
    exit 1
}

if (-not $Key) {
    foreach ($candidate in @("$HOME\.ssh\id_ed25519.pub", "$HOME\.ssh\id_rsa.pub")) {
        if (Test-Path $candidate) { $Key = $candidate; break }
    }
}
if (-not $Key -or -not (Test-Path $Key)) {
    Write-Host "No public key found. Give one with -Key, for example:" -ForegroundColor Red
    Write-Host "  .\rescue_ssh.ps1 -Path $Path -Key `$HOME\.ssh\id_ed25519.pub"
    exit 1
}
$SshPubKey = (Get-Content $Key -Raw).Trim()
if ($SshPubKey -notmatch '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521))\s') {
    Write-Host "$Key does not look like a public key (a private key, or a truncated file?)." -ForegroundColor Red
    exit 1
}
$KeyB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($SshPubKey))

$Template = @'
#!/bin/bash
# One-shot rescue written by bootstrap/rescue_ssh.ps1: reports what the account
# looks like, installs the key below, then removes itself and reboots.

BOOT_DIR="/boot/firmware"
[ -d "$BOOT_DIR" ] || BOOT_DIR="/boot"

LOG="$BOOT_DIR/rescue.log"
exec > "$LOG" 2>&1
echo "$(date) - rukebox rescue"
echo "hostname=$(hostname)  /etc/hostname=$(cat /etc/hostname 2>/dev/null)"
echo ""
echo "== boot partition =="
ls -la "$BOOT_DIR"
echo ""
echo "== accounts =="
getent passwd 1000
getent passwd pi
echo "all users with a home:"
getent passwd | awk -F: '$3 >= 1000 && $3 < 65000 {print $1" "$3" "$6" "$7}'
echo "authorized_keys found anywhere in /home:"
sudo find /home -name authorized_keys -exec ls -l {} \; 2>/dev/null
echo ""
echo "== pi: shell, password state, groups =="
getent passwd pi | cut -d: -f7
passwd -S pi 2>&1
id pi 2>&1
echo ""
echo "== homes =="
ls -la /home
HOME_DIR="$(getent passwd pi | cut -d: -f6)"
echo "pi home: $HOME_DIR"
ls -la "$HOME_DIR" 2>&1
ls -la "$HOME_DIR/.ssh" 2>&1
echo "keys there:"
ssh-keygen -lf "$HOME_DIR/.ssh/authorized_keys" 2>&1
echo ""
echo "== account service =="
systemctl is-enabled rukebox-account.service 2>&1
systemctl is-active rukebox-account.service 2>&1
ls -la /etc/rukebox/account-setup.env 2>&1
sed 's/_B64=.*/_B64=<hidden>/' /etc/rukebox/account-setup.env 2>/dev/null
echo ""
echo "== cloud-init =="
cloud-init status --long 2>&1 | head -20
ls -la /boot/firmware/user-data /boot/firmware/meta-data 2>&1
echo ""
echo "== sshd =="
grep -rE '^(PubkeyAuthentication|AuthorizedKeysFile|PasswordAuthentication)' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/ 2>&1
echo ""
echo "== firstrun.log (tail) =="
tail -25 "$BOOT_DIR/firstrun.log" 2>&1
echo ""
echo "== rukebox-account.log (tail) =="
tail -30 "$BOOT_DIR/rukebox-account.log" 2>&1
echo ""
echo "== installing the key =="
KEY="$(echo '__KEY_B64__' | base64 -d)"
if [ -n "$(id -u pi 2>/dev/null)" ]; then
    GROUP="$(id -gn pi)"
    usermod -s /bin/bash pi
    install -d -m 700 -o pi -g "$GROUP" "$HOME_DIR/.ssh"
    printf '%s\n' "$KEY" >> "$HOME_DIR/.ssh/authorized_keys"
    sort -u "$HOME_DIR/.ssh/authorized_keys" -o "$HOME_DIR/.ssh/authorized_keys"
    chown -R "pi:$GROUP" "$HOME_DIR/.ssh"
    chmod 700 "$HOME_DIR/.ssh"
    chmod 600 "$HOME_DIR/.ssh/authorized_keys"
    ls -la "$HOME_DIR/.ssh/authorized_keys"
    ssh-keygen -lf "$HOME_DIR/.ssh/authorized_keys"
    systemctl restart ssh 2>&1 || true
else
    echo "NO pi ACCOUNT: nothing installed"
fi

echo "== cleaning up =="
CMDLINE="$BOOT_DIR/cmdline.txt"
[ -f "$CMDLINE" ] && sed -i -E 's/ ?systemd\.(run|run_success_action|unit)=[^ ]*//g' "$CMDLINE"
rm -f "$BOOT_DIR/firstrun.sh"
echo "$(date) - rescue done, rebooting"
reboot
'@

$Firstrun = $Template.Replace("__KEY_B64__", $KeyB64).Replace("`r`n", "`n")
[IO.File]::WriteAllText("$Path\firstrun.sh", $Firstrun, (New-Object Text.UTF8Encoding($false)))

$Cmdline = (Get-Content "$Path\cmdline.txt" -Raw).Trim()
$Cmdline = $Cmdline -replace ' ?systemd\.(run|run_success_action|unit)=\S*', ''
$Cmdline += ' systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target'
[IO.File]::WriteAllText("$Path\cmdline.txt", $Cmdline, (New-Object Text.UTF8Encoding($false)))

Write-Host ""
Write-Host "Rescue written to $Path" -ForegroundColor Green
Write-Host "  1. Eject the card safely, put it back in the Pi, power it on."
Write-Host "  2. The Pi reports what it found in rescue.log on the card, installs"
Write-Host "     $Key, and reboots by itself once."
Write-Host "  3. Forget the old host keys, then log in:"
Write-Host "       ssh-keygen -R rukebox.local"
Write-Host "       ssh-keygen -R 169.254.7.7"
Write-Host "       ssh-keygen -R <the Pi's address on your network>"
Write-Host "       ssh pi@169.254.7.7"
Write-Host ""
Write-Host "Bring rescue.log back here: it says why the account setup did not run."
