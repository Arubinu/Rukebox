#Requires -Version 5

param(

    [string]$PiHost = "169.254.7.7",
    [string]$PiUser = "pi",
    [int]$Port = 22,
    [string]$Identity = "",
    [switch]$NoRestart,
    [switch]$DryRun,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"

Write-Host "=== Rukebox - pushing an update over USB ===" -ForegroundColor Cyan
Write-Host ""

foreach ($tool in @("tar.exe", "ssh.exe", "scp.exe")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Host "${tool} not found on this computer." -ForegroundColor Red
        Write-Host ""
        Write-Host "tar is included in Windows 10 (1803+) and Windows 11."
        Write-Host "ssh/scp come from the 'OpenSSH Client' optional feature:"
        Write-Host "  Settings > System > Optional features > Add > OpenSSH Client"
        exit 1
    }
}

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
foreach ($required in @("src\rukebox_daemon.py", "web\index.html", "scripts\update.sh", "config\rukebox.yaml")) {
    if (-not (Test-Path (Join-Path $ProjectRoot $required))) {
        Write-Host "${required} missing from ${ProjectRoot} - run this script from the project." -ForegroundColor Red
        exit 1
    }
}

$SshOpts = @("-o", "ConnectTimeout=10", "-p", "$Port")
$ScpOpts = @("-o", "ConnectTimeout=10", "-P", "$Port")
if (-not [string]::IsNullOrWhiteSpace($Identity)) {
    $SshOpts += @("-i", $Identity)
    $ScpOpts += @("-i", $Identity)
}
$PiHostExplicit = $PSBoundParameters.ContainsKey('PiHost')
$Target = "${PiUser}@${PiHost}"

$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Archive = Join-Path $env:TEMP "rukebox-update-${Stamp}.tar.gz"
$ApplyLocal = Join-Path $env:TEMP "rukebox-apply-${Stamp}.sh"

Write-Host "== Packing ${ProjectRoot} =="
# What an update can install is src/, scripts/, web/, config/, systemd/ and
# assets/sounds/ (see src/version.py): everything else here is the repository
# itself - the docs, the icon sources, the card-setup build, the tests - and
# sending it made every push carry ~23 MB that the Pi never looks at.
$tarArgs = @(
    "-czf", $Archive,
    "-C", $ProjectRoot,
    "--exclude=.git",
    "--exclude=__pycache__",
    "--exclude=*.pyc",
    "--exclude=*.pyo",
    "--exclude=.DS_Store",
    "--exclude=node_modules",
    "--exclude=*.tar.gz",
    "--exclude=./.venv",
    "--exclude=./graphify-out",
    "--exclude=./src/graphify-out",
    "--exclude=./assets/icons",
    "--exclude=./bootstrap",
    "--exclude=./dist",
    "--exclude=./docs",
    "--exclude=./tests",
    "--exclude=./.claude",
    "--exclude=./CLAUDE.md",
    "--exclude=./TODO.md",
    "--exclude=./README.md",
    "--exclude=./README.*.md",
    "."
)
& tar.exe $tarArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "Could not build the archive." -ForegroundColor Red
    exit 1
}
$SizeKb = [math]::Round((Get-Item $Archive).Length / 1KB)
Write-Host "   archive: ${SizeKb} KB"

$LocalVersion = ""
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command python3 -ErrorAction SilentlyContinue }
if ($python) {
    $shown = & $python.Source (Join-Path $ProjectRoot "src\version.py") "show" $ProjectRoot
    if ($LASTEXITCODE -eq 0) {
        foreach ($line in $shown) {
            if ($line -match '"tree_hash_short":\s*"([^"]+)"') { $LocalVersion = $Matches[1] }
        }
    }
    if ($LocalVersion) { Write-Host "   version: ${LocalVersion}" }
}

if ($DryRun) {
    Write-Host ""
    Write-Host "-DryRun: nothing sent. Content that would be pushed:"
    $listing = & tar.exe -tzf $Archive
    $listing | Select-Object -First 40 | ForEach-Object { Write-Host "   $_" }
    Write-Host "   ..."
    Remove-Item $Archive -Force -ErrorAction SilentlyContinue
    exit 0
}

Write-Host ""
Write-Host "== Connecting to ${Target} =="
$OriginalTarget = $Target
& ssh.exe @SshOpts $Target "true"
$Reached = ($LASTEXITCODE -eq 0)

if (-not $Reached -and -not $PiHostExplicit -and $PiHost -ne "rukebox.local") {

    $FallbackTarget = "${PiUser}@rukebox.local"
    Write-Host "   ${Target} not reachable, trying ${FallbackTarget} (mDNS) ..."
    & ssh.exe @SshOpts $FallbackTarget "true"
    if ($LASTEXITCODE -eq 0) {
        $PiHost = "rukebox.local"
        $Target = $FallbackTarget
        $Reached = $true
    }
}

if (-not $Reached) {
    Write-Host ""
    if ($Target -ne $OriginalTarget) {
        Write-Host "Could not reach ${OriginalTarget} or ${Target} over SSH." -ForegroundColor Red
    } else {
        Write-Host "Could not reach ${Target} over SSH." -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "Checks:"
    Write-Host "  - USB cable plugged into the Pi's DATA port (not PWR)"
    Write-Host "  - Pi powered on and finished booting"
    Write-Host "  - ssh ${OriginalTarget} works on its own"
    Remove-Item $Archive -Force -ErrorAction SilentlyContinue
    exit 1
}

& ssh.exe @SshOpts $Target "test -d /opt/rukebox"
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "${Target} is reachable, but Rukebox isn't installed there (/opt/rukebox missing)." -ForegroundColor Red
    Write-Host "For a first installation, see README, 'Installing on a blank SD card',"
    Write-Host "or use bootstrap\push_install.ps1."
    Remove-Item $Archive -Force -ErrorAction SilentlyContinue
    exit 1
}

$RemoteVersion = ""
$versionOut = & ssh.exe @SshOpts $Target "cat /var/lib/rukebox/version.json 2>/dev/null"
if ($LASTEXITCODE -eq 0) {
    foreach ($line in $versionOut) {
        if ($line -match '"tree_hash_short":\s*"([^"]+)"') { $RemoteVersion = $Matches[1] }
    }
}
if ($RemoteVersion) {
    Write-Host "   installed version: ${RemoteVersion}"
} else {
    Write-Host "   installed version: unknown"
}
if ($RemoteVersion -and $LocalVersion -and ($RemoteVersion -eq $LocalVersion)) {
    Write-Host ""
    Write-Host "The Pi already runs this exact version. Nothing to do."
    Remove-Item $Archive -Force -ErrorAction SilentlyContinue
    exit 0
}

if (-not $Yes) {
    Write-Host ""
    if ($LocalVersion) {
        Write-Host "About to update ${Target} to ${LocalVersion}."
    } else {
        Write-Host "About to update ${Target} to this version."
    }
    Write-Host "Music, settings and statistics on the Pi are preserved."
    $confirm = Read-Host "Continue? (Y/n)"
    if ($confirm -match '^[nN]') {
        Write-Host "Cancelled."
        Remove-Item $Archive -Force -ErrorAction SilentlyContinue
        exit 0
    }
}

$RemoteArchive = "/tmp/rukebox-update-${Stamp}.tar.gz"
$RemoteApply = "/tmp/rukebox-apply-${Stamp}.sh"

Write-Host ""
Write-Host "== Sending (${SizeKb} KB) =="
& scp.exe @ScpOpts $Archive "${Target}:${RemoteArchive}"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Transfer failed." -ForegroundColor Red
    Remove-Item $Archive -Force -ErrorAction SilentlyContinue
    exit 1
}

$ExtraArgs = ""
if ($NoRestart) { $ExtraArgs = " --no-restart" }

$ApplyTemplate = @'
set -e
STAGING="$(mktemp -d /tmp/rukebox-staging-XXXXXX)"
cleanup() {
    # update.sh just ran under sudo and may have left root-owned files
    # in here (e.g. __pycache__/*.pyc from importing the staged Python
    # source) that this script, running as the plain SSH user, cannot
    # remove - that must never be mistaken for the update itself
    # failing, which is why every branch below ends in "|| true".
    sudo rm -rf "$STAGING" 2>/dev/null || rm -rf "$STAGING" 2>/dev/null || true
    rm -f "__ARCHIVE__" "__APPLY__"
}
trap cleanup EXIT
tar xzf "__ARCHIVE__" -C "$STAGING"
chmod +x "$STAGING/scripts/update.sh"
sudo "$STAGING/scripts/update.sh" --source "$STAGING"__EXTRA__
'@
$Apply = $ApplyTemplate.Replace("__ARCHIVE__", $RemoteArchive).Replace("__APPLY__", $RemoteApply).Replace("__EXTRA__", $ExtraArgs)

$Apply = $Apply -replace "`r`n", "`n"
[System.IO.File]::WriteAllText($ApplyLocal, $Apply, (New-Object System.Text.UTF8Encoding $false))

& scp.exe @ScpOpts $ApplyLocal "${Target}:${RemoteApply}"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Transfer failed." -ForegroundColor Red
    Remove-Item $Archive, $ApplyLocal -Force -ErrorAction SilentlyContinue
    exit 1
}

Write-Host ""
Write-Host "== Updating on the Pi =="

& ssh.exe -t @SshOpts $Target "sh ${RemoteApply}"
$updateExit = $LASTEXITCODE

Remove-Item $Archive, $ApplyLocal -Force -ErrorAction SilentlyContinue

if ($updateExit -ne 0) {
    Write-Host ""
    Write-Host "The update failed. The Pi restored its previous version by itself." -ForegroundColor Red
    Write-Host "Details:  ssh ${Target} 'journalctl -u rukebox-daemon.service -n 50'"
    exit 1
}

Write-Host ""
Write-Host "== Done ==" -ForegroundColor Green
if ($LocalVersion) {
    Write-Host "The Pi now runs ${LocalVersion}."
} else {
    Write-Host "The Pi now runs the pushed version."
}
Write-Host ""
Write-Host "Checks:"
Write-Host "  ssh ${Target} 'systemctl status rukebox-daemon.service'"
Write-Host "  Web interface: the Statistics card shows the installed version."
Write-Host ""
Write-Host "To go back:  ssh ${Target} 'sudo /usr/local/sbin/rukebox-update --rollback'"
