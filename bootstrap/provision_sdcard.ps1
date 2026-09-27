#Requires -Version 5

$ErrorActionPreference = "Stop"

Write-Host "=== Rukebox - provisioning a blank SD card ===" -ForegroundColor Cyan
Write-Host ""

$Detected = @()
Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue | ForEach-Object {
    $root = $_.Root
    if (Test-Path (Join-Path $root "config.txt")) {
        $Detected += $root.TrimEnd('\')
    }
}

$BootPath = ""
if ($Detected.Count -eq 1) {
    Write-Host "Detected boot partition: $($Detected[0])"
    $confirm = Read-Host "Use it? (Y/n)"
    if ($confirm -notmatch '^[nN]') { $BootPath = $Detected[0] }
} elseif ($Detected.Count -gt 1) {
    Write-Host "Several candidate boot partitions found:"
    for ($i = 0; $i -lt $Detected.Count; $i++) {
        Write-Host "  $($i+1)) $($Detected[$i])"
    }
    $choice = Read-Host "Number to use, or Enter to type a path manually"
    if ($choice -match '^\d+$' -and [int]$choice -ge 1 -and [int]$choice -le $Detected.Count) {
        $BootPath = $Detected[[int]$choice - 1]
    }
}

if ([string]::IsNullOrWhiteSpace($BootPath)) {
    $BootPath = Read-Host "Path or drive letter of the boot partition (e.g. D:\)"
}
$BootPath = $BootPath.TrimEnd('\')
if (-not (Test-Path "$BootPath\config.txt")) {
    Write-Error "config.txt not found in $BootPath - this doesn't look like the boot partition of a Raspberry Pi OS image."
    exit 1
}

$SecurePw1 = Read-Host "Password for the 'pi' user (leave empty for SSH key access only)" -AsSecureString
$SecurePw2 = Read-Host "Confirm the password" -AsSecureString
$Bstr1 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecurePw1)
$Bstr2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecurePw2)
$PlainPw1 = [Runtime.InteropServices.Marshal]::PtrToStringAuto($Bstr1)
$PlainPw2 = [Runtime.InteropServices.Marshal]::PtrToStringAuto($Bstr2)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Bstr1)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Bstr2)
if ($PlainPw1 -ne $PlainPw2) {
    Write-Error "Passwords don't match."
    exit 1
}

$DefaultKey = "$env:USERPROFILE\.ssh\id_ed25519.pub"
if (-not (Test-Path $DefaultKey)) { $DefaultKey = "$env:USERPROFILE\.ssh\id_rsa.pub" }
$SshKeyPath = Read-Host "Path to your SSH public key (empty to skip) [$DefaultKey]"
if ([string]::IsNullOrWhiteSpace($SshKeyPath)) { $SshKeyPath = $DefaultKey }
$SshPubKey = ""
if (Test-Path $SshKeyPath) {
    $SshPubKey = (Get-Content $SshKeyPath -Raw).Trim()
    Write-Host "SSH key found: $SshKeyPath"
} else {
    Write-Host "No key found."
}

if ([string]::IsNullOrEmpty($PlainPw1) -and [string]::IsNullOrEmpty($SshPubKey)) {
    Write-Error "Neither a password nor an SSH key: there would be no way to log into the Pi. Provide at least one of the two."
    exit 1
}
if ([string]::IsNullOrEmpty($PlainPw1)) {
    Write-Host "No password: the account will be locked, SSH key login only." -ForegroundColor Yellow
}

$HostnameInput = Read-Host "Pi hostname [rukebox]"
if ([string]::IsNullOrWhiteSpace($HostnameInput)) { $HostnameInput = "rukebox" }

Write-Host ""
Write-Host "== Writing configuration to the card ==" -ForegroundColor Cyan

New-Item -Path "$BootPath\ssh" -ItemType File -Force | Out-Null

$Utf8NoBom = New-Object System.Text.UTF8Encoding $false

function Get-Sha512CryptHash($PlainText) {
    if (Get-Command openssl -ErrorAction SilentlyContinue) {
        $result = ($PlainText | & openssl passwd -6 -stdin 2>$null)
        if ($LASTEXITCODE -eq 0 -and $result) { return $result.Trim() }
    }
    if (Get-Command wsl -ErrorAction SilentlyContinue) {
        $result = ($PlainText | & wsl openssl passwd -6 -stdin 2>$null)
        if ($LASTEXITCODE -eq 0 -and $result) { return $result.Trim() }
    }
    return $null
}

$FixedPlaceholderHash = '$6$raspberryradiose$sIhB.tiKCeajySW5V7Cxf.HEcDgTxmNScaWIf4ZvLug1ICXRdWe7Gy47YOpnYJUmaIrDqg/BpmQzMFeI5QKmp1'
if ([string]::IsNullOrEmpty($PlainPw1)) {
    $PwHash = $FixedPlaceholderHash
} else {
    $PwHash = Get-Sha512CryptHash $PlainPw1
    if ([string]::IsNullOrEmpty($PwHash)) {
        Write-Error "Could not hash the password: openssl not found (not even via WSL). Install OpenSSL (e.g. via Git for Windows) or WSL, or leave the password empty and use an SSH key instead."
        exit 1
    }
}
[System.IO.File]::WriteAllText("$BootPath\userconf.txt", "pi:$PwHash`n", $Utf8NoBom)

$ConfigLines = Get-Content "$BootPath\config.txt"
if (-not ($ConfigLines -match "^# rukebox: USB gadget mode")) {
    Add-Content -Path "$BootPath\config.txt" -Value @(
        "",
        "[all]",
        "# rukebox: USB gadget mode - makes the Pi appear as a USB network",
        "# interface, so SSH works over the USB cable alone.",
        "dtoverlay=dwc2,dr_mode=otg"
    )
}

$CmdlinePath = "$BootPath\cmdline.txt"
Copy-Item $CmdlinePath "$CmdlinePath.orig" -Force
$CmdlineContent = (Get-Content $CmdlinePath -Raw).Trim()

if ($CmdlineContent -notmatch "modules-load=dwc2") {
    $CmdlineContent = $CmdlineContent -replace "rootwait", "rootwait modules-load=dwc2"
}

if ((Test-Path "$BootPath\firstrun.sh") -and -not (Select-String -Path "$BootPath\firstrun.sh" -Pattern "rukebox" -Quiet)) {
    Move-Item "$BootPath\firstrun.sh" "$BootPath\rukebox-imager-firstrun.sh" -Force
}
$CmdlineContent = $CmdlineContent -replace " ?systemd\.run=\S*", "" -replace " ?systemd\.run_success_action=\S*", "" -replace " ?systemd\.unit=\S*", ""
$CmdlineContent = "$CmdlineContent systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target"
[System.IO.File]::WriteAllText($CmdlinePath, $CmdlineContent, $Utf8NoBom)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DestProject = "$BootPath\rukebox"
if (Test-Path $DestProject) { Remove-Item $DestProject -Recurse -Force }
Copy-Item $ProjectRoot $DestProject -Recurse -Force
if (Test-Path "$DestProject\.git") { Remove-Item "$DestProject\.git" -Recurse -Force }

function ConvertTo-Base64String($PlainText) {
    if ([string]::IsNullOrEmpty($PlainText)) { return "" }
    [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($PlainText))
}
$PwB64 = ConvertTo-Base64String $PlainPw1
$SshKeyB64 = ConvertTo-Base64String $SshPubKey

$TemplateContent = Get-Content "$PSScriptRoot\firstrun.sh.template" -Raw
$TemplateContent = $TemplateContent.Replace("__HOSTNAME__", $HostnameInput)

$TemplateContent = $TemplateContent -replace "`r`n", "`n"
[System.IO.File]::WriteAllText("$BootPath\firstrun.sh", $TemplateContent, $Utf8NoBom)

$AccountEnv = @(
    "ACCOUNT_USER=pi"
    "ACCOUNT_MANAGE=yes"
    "ACCOUNT_PASSWORD_B64='$PwB64'"
    "ACCOUNT_SSH_KEY_B64='$SshKeyB64'"
) -join "`n"
[System.IO.File]::WriteAllText("$BootPath\rukebox-account.env", $AccountEnv + "`n", $Utf8NoBom)

Write-Host ""
Write-Host "== Done ==" -ForegroundColor Green
Write-Host "Safely eject the SD card ('Eject' in File Explorer), insert it"
Write-Host "into the Pi and power it on."
Write-Host ""
Write-Host "The Pi boots, runs the provisioning script automatically, then"
Write-Host "REBOOTS ITSELF once done - you don't need to do anything, just"
Write-Host "wait about 1-2 minutes."
Write-Host ""
Write-Host "Then:"
Write-Host "1. Connect the Pi to this computer with a USB cable (the 'DATA'"
Write-Host "   USB port, not 'PWR', on a Pi Zero/Zero 2 W)."
Write-Host "2. ssh pi@169.254.7.7"
Write-Host "   (fixed link-local address, works out of the box on Windows -"
Write-Host "   no mDNS/Bonjour needed. Can also try ssh pi@${HostnameInput}.local"
Write-Host "   as a fallback if you already have Bonjour installed.)"
Write-Host ""
Write-Host "IMPORTANT: the radio software isn't installed yet at this stage" -ForegroundColor Yellow
Write-Host "(that needs Internet access). See the README, 'Installing on a" -ForegroundColor Yellow
Write-Host "blank SD card' section, for what's next." -ForegroundColor Yellow
