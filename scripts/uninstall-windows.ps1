[CmdletBinding()]
param(
    [string]$InstallRoot = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not $InstallRoot) {
    $InstallRoot = $env:TOKEN_METER_INSTALL_ROOT
}
if (-not $InstallRoot) {
    $InstallRoot = Join-Path $env:LOCALAPPDATA "Token Meter\runtime"
}
$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$StartScript = Join-Path $InstallRoot "scripts\start-token-meter.ps1"
$RunKey = "Registry::HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run"
$RunName = "Token Meter"

$RunValue = $null
if (Test-Path -LiteralPath $RunKey) {
    $RunProperties = Get-ItemProperty -LiteralPath $RunKey -Name $RunName -ErrorAction SilentlyContinue
    if ($RunProperties) {
        $RunValue = $RunProperties.$RunName
    }
}
if ($RunValue) {
    if ([string]$RunValue -notlike "*$StartScript*") {
        throw "A different Token Meter installation owns the login startup entry."
    }
    Remove-ItemProperty -LiteralPath $RunKey -Name $RunName
}

$PidPath = Join-Path $InstallRoot "meter.pid"
if (Test-Path -LiteralPath $PidPath -PathType Leaf) {
    $ServerPid = 0
    if ([int]::TryParse((Get-Content -LiteralPath $PidPath -Raw).Trim(), [ref]$ServerPid)) {
        $Process = Get-CimInstance Win32_Process -Filter "ProcessId = $ServerPid" -ErrorAction SilentlyContinue
        $ExpectedMeter = [System.IO.Path]::GetFullPath((Join-Path $InstallRoot "meter.py"))
        if ($Process) {
            if ([string]$Process.CommandLine -notlike "*$ExpectedMeter*") {
                throw "The saved server process ID belongs to a different program."
            }
            Stop-Process -Id $ServerPid -Force
            try {
                Wait-Process -Id $ServerPid -Timeout 10 -ErrorAction SilentlyContinue
            } catch { }
        }
    }
    Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
}

Write-Host "Token Meter automatic startup removed and the local server stopped."
Write-Host "Runtime retained at: $InstallRoot"
