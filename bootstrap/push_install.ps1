#Requires -Version 5

param(

    [string]$PiHost = "169.254.7.7",
    [string]$PiUser = "pi",
    [int]$Port = 22,
    [string]$Identity = "",
    [switch]$DryRun,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"

Write-Host "=== Rukebox - pushing the first installation ===" -ForegroundColor Cyan
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
foreach ($required in @("src\rukebox_daemon.py", "web\index.html", "scripts\install.sh", "config\rukebox.yaml")) {
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
$Archive = Join-Path $env:TEMP "rukebox-install-${Stamp}.tar.gz"
$ApplyLocal = Join-Path $env:TEMP "rukebox-install-apply-${Stamp}.sh"

Write-Host "== Packing ${ProjectRoot} =="
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
    "--exclude=./*.tgz",
    "--exclude=./.venv",
    "--exclude=./graphify-out",
    "--exclude=./src/graphify-out",
    "--exclude=./assets/icons",
    "--exclude=./bootstrap",
    "--exclude=./dist",
    "--exclude=./docs",
    "--exclude=./tests",
    "--exclude=./.claude",
    "--exclude=./.scratch",
    "--exclude=./docker/data",
    "--exclude=./docker/config",
    "--exclude=./CLAUDE.md",
    "--exclude=./TODO.md",
    "--exclude=./README.md",
    "--exclude=./README.*.md",
    "--exclude=./node_modules",
    "--exclude=./package.json",
    "--exclude=./package-lock.json",
    "--exclude=./.github",
    "."
)
& tar.exe $tarArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "Could not build the archive." -ForegroundColor Red
    exit 1
}
$SizeKb = [math]::Round((Get-Item $Archive).Length / 1KB)
Write-Host "   archive: ${SizeKb} KB"

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
    Write-Host "  - over USB: cable plugged into the Pi's DATA port (not PWR),"
    Write-Host "    Pi powered on and finished booting"
    Write-Host "  - over the network: -PiHost/-PiUser point at the right Pi, and"
    Write-Host "    it already has SSH enabled and reachable (see README,"
    Write-Host "    'Installing on a blank SD card')"
    Write-Host "  - ssh ${OriginalTarget} works on its own"
    Remove-Item $Archive -Force -ErrorAction SilentlyContinue
    exit 1
}

& ssh.exe @SshOpts $Target "test -d /opt/rukebox"
$alreadyInstalled = ($LASTEXITCODE -eq 0)
if ($alreadyInstalled) {
    Write-Host "   Rukebox already appears to be installed there."
    if (-not $Yes) {
        $confirm = Read-Host "Reinstall/repair anyway? Settings, music and statistics are preserved. (y/N)"
        if ($confirm -notmatch '^[yY]') {
            Write-Host "Cancelled."
            Remove-Item $Archive -Force -ErrorAction SilentlyContinue
            exit 0
        }
    }
} elseif (-not $Yes) {
    Write-Host ""
    Write-Host "About to install Rukebox on ${Target}."
    $confirm = Read-Host "Continue? (Y/n)"
    if ($confirm -match '^[nN]') {
        Write-Host "Cancelled."
        Remove-Item $Archive -Force -ErrorAction SilentlyContinue
        exit 0
    }
}

$RemoteArchive = "/tmp/rukebox-install-${Stamp}.tar.gz"
$RemoteApply = "/tmp/rukebox-install-apply-${Stamp}.sh"

Write-Host ""
Write-Host "== Sending (${SizeKb} KB) =="
& scp.exe @ScpOpts $Archive "${Target}:${RemoteArchive}"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Transfer failed." -ForegroundColor Red
    Remove-Item $Archive -Force -ErrorAction SilentlyContinue
    exit 1
}

$ApplyTemplate = @'
set -e
STAGING="$(mktemp -d /tmp/rukebox-staging-XXXXXX)"
cleanup() {
    # install.sh just ran under sudo and may have left root-owned files
    # in here (e.g. __pycache__/*.pyc from importing the staged Python
    # source) that this script, running as the plain SSH user, cannot
    # remove - that must never be mistaken for the install itself
    # failing, which is why every branch below ends in "|| true".
    sudo rm -rf "$STAGING" 2>/dev/null || rm -rf "$STAGING" 2>/dev/null || true
    rm -f "__ARCHIVE__" "__APPLY__"
}
trap cleanup EXIT
tar xzf "__ARCHIVE__" -C "$STAGING"
chmod +x "$STAGING/scripts/"*.sh
sudo "$STAGING/scripts/install.sh"
'@
$Apply = $ApplyTemplate.Replace("__ARCHIVE__", $RemoteArchive).Replace("__APPLY__", $RemoteApply)

$Apply = $Apply -replace "`r`n", "`n"
[System.IO.File]::WriteAllText($ApplyLocal, $Apply, (New-Object System.Text.UTF8Encoding $false))

& scp.exe @ScpOpts $ApplyLocal "${Target}:${RemoteApply}"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Transfer failed." -ForegroundColor Red
    Remove-Item $Archive, $ApplyLocal -Force -ErrorAction SilentlyContinue
    exit 1
}

Write-Host ""
Write-Host "== Installing on the Pi (needs the Pi's own Internet access - apt/pip) =="

& ssh.exe -t @SshOpts $Target "sh ${RemoteApply}"
$installExit = $LASTEXITCODE

Remove-Item $Archive, $ApplyLocal -Force -ErrorAction SilentlyContinue

if ($installExit -ne 0) {
    Write-Host ""
    Write-Host "The installation failed - see the output above." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "== Done ==" -ForegroundColor Green
Write-Host "Next steps (see the installer's own output above for the full list):"
Write-Host "  ssh ${Target}"
Write-Host "  sudo nano /etc/rukebox/rukebox.yaml   # speaker MAC, times, folders"
