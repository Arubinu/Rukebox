#Requires -Version 5

param(
    [switch]$Remove,

    [switch]$Pause,
    [string]$LogFile = ""
)

$ErrorActionPreference = "Stop"

$AccessMac   = "02-1A-11-00-00-01"
$InternetMac = "02-1A-11-00-01-01"
$AccessName   = "Rukebox (USB)"
$InternetName = "Rukebox (Internet)"
$HostAddress  = "192.168.77.1"
$Prefix       = "192.168.77.0/24"
$NatName      = "Rukebox"

$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Administrator rights are needed - Windows will ask for them." -ForegroundColor Yellow
    $argsList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($Remove) { $argsList += "-Remove" }
    if ($LogFile) { $argsList += @("-LogFile", "`"$LogFile`"") } else { $argsList += "-Pause" }
    try {
        Start-Process powershell -Verb RunAs -ArgumentList $argsList -Wait
    } catch {
        Write-Host "Cancelled: nothing was changed." -ForegroundColor Red
        exit 1
    }
    exit 0
}

function Finish([int]$code) {
    if ($LogFile) { Stop-Transcript | Out-Null }
    if ($Pause) { Write-Host ""; Read-Host "Press Enter to close" | Out-Null }
    exit $code
}

function Find-Card([string]$mac) {
    Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.MacAddress -eq $mac } | Select-Object -First 1
}

function Name-Card($card, [string]$name) {
    if ($card -and $card.Name -ne $name) {

        $other = Get-NetAdapter -Name $name -ErrorAction SilentlyContinue
        if ($other -and $other.MacAddress -ne $card.MacAddress) {
            Rename-NetAdapter -Name $name -NewName ($name + " (old)")
        }
        Rename-NetAdapter -Name $card.Name -NewName $name
        Write-Host "  '$($card.Name)' is now called '$name'"
    }
}

if ($LogFile) { Start-Transcript -Path $LogFile -Force | Out-Null }
Write-Host "=== Rukebox - Internet over the USB cable ===" -ForegroundColor Cyan
Write-Host ""

$access   = Find-Card $AccessMac
$internet = Find-Card $InternetMac

try {
    $share = New-Object -ComObject HNetCfg.HNetShare
    $rukeboxNames = @()
    if ($access)   { $rukeboxNames += $access.Name;   $rukeboxNames += $access.InterfaceDescription }
    if ($internet) { $rukeboxNames += $internet.Name; $rukeboxNames += $internet.InterfaceDescription }
    $shared = @()
    $towardsRukebox = $false
    foreach ($c in $share.EnumEveryConnection) {
        $props = $share.NetConnectionProps.Invoke($c)
        $conf = $share.INetSharingConfigurationForINetConnection.Invoke($c)
        if ($conf.SharingEnabled) {
            $shared += @{ Name = $props.Name; Conf = $conf }

            if ($conf.SharingConnectionType -eq 1 -and ($rukeboxNames -contains $props.Name -or $rukeboxNames -contains $props.DeviceName)) {
                $towardsRukebox = $true
            }
        }
    }
    if ($towardsRukebox) {
        foreach ($s in $shared) { $s.Conf.DisableSharing(); Write-Host "  Internet Connection Sharing turned off on '$($s.Name)'" }
    }
} catch {
    Write-Host "  (could not check Internet Connection Sharing: $($_.Exception.Message))" -ForegroundColor DarkGray
}

if ($Remove) {
    $nat = Get-NetNat -Name $NatName -ErrorAction SilentlyContinue
    if ($nat) { Remove-NetNat -Name $NatName -Confirm:$false; Write-Host "  NAT '$NatName' removed" }
    if ($internet) {
        Get-NetIPAddress -InterfaceIndex $internet.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
        Set-NetIPInterface -InterfaceIndex $internet.ifIndex -AddressFamily IPv4 -Dhcp Enabled
        Write-Host "  '$($internet.Name)' is back to automatic addressing"
    }
    Write-Host ""
    Write-Host "Done: the Rukebox no longer reaches the Internet through this computer." -ForegroundColor Green
    Finish 0
}

if (-not $access -and -not $internet) {
    Write-Host "The Rukebox is not plugged in (no USB network card found)." -ForegroundColor Red
    Write-Host "Plug the cable into the Pi's USB data port and run this again."
    Finish 1
}
if (-not $internet) {
    Write-Host "Only the SSH / web card is there: the Pi runs an older version." -ForegroundColor Red
    Write-Host "Update it first (bootstrap\push_update.cmd), then run this again."
    Finish 1
}

Name-Card $access $AccessName
Name-Card $internet $InternetName
$internet = Find-Card $InternetMac

$current = Get-NetIPAddress -InterfaceIndex $internet.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
if (-not ($current | Where-Object { $_.IPAddress -eq $HostAddress })) {
    $current | Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
    Set-NetIPInterface -InterfaceIndex $internet.ifIndex -AddressFamily IPv4 -Dhcp Disabled

    New-NetIPAddress -InterfaceIndex $internet.ifIndex -IPAddress $HostAddress -PrefixLength 24 | Out-Null
    Write-Host "  '$InternetName' has the address $HostAddress/24"
}

$nats = @(Get-NetNat -ErrorAction SilentlyContinue)
if (-not ($nats | Where-Object { $_.InternalIPInterfaceAddressPrefix -eq $Prefix })) {
    try {
        New-NetNat -Name $NatName -InternalIPInterfaceAddressPrefix $Prefix | Out-Null
        Write-Host "  NAT '$NatName' created for $Prefix"
    } catch {
        Write-Host "Could not create the NAT: $($_.Exception.Message)" -ForegroundColor Red
        if ($nats.Count -gt 0) {
            Write-Host "Some Windows versions allow only one NAT, and one already exists:"
            $nats | ForEach-Object { Write-Host "  - $($_.Name) ($($_.InternalIPInterfaceAddressPrefix))" }
        }
        Finish 1
    }
}

Write-Host ""
Write-Host "Done. The Rukebox now reaches the Internet through this computer." -ForegroundColor Green
Write-Host "  SSH and web interface : http://169.254.7.7/  (card '$AccessName')"
Write-Host "  Check from the Pi     : ssh pi@169.254.7.7 'ping -c 2 1.1.1.1'"
Write-Host "  To undo               : bootstrap\usb_internet.cmd -Remove"
Finish 0
