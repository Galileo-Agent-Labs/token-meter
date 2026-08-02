[CmdletBinding()]
param(
    [int]$ReadinessTimeoutSeconds = 0
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-Health {
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:8722/health" -TimeoutSec 3
    } catch {
        return $null
    }
}

function Test-OwnedHealth($Health, [string]$ExpectedPage) {
    if (-not $Health -or -not $Health.page_path) {
        return $false
    }
    try {
        return [System.IO.Path]::GetFullPath([string]$Health.page_path) -eq $ExpectedPage
    } catch {
        return $false
    }
}

$RuntimeRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$MeterPath = [System.IO.Path]::GetFullPath((Join-Path $RuntimeRoot "meter.py"))
$ExpectedPage = [System.IO.Path]::GetFullPath((Join-Path $RuntimeRoot "page.html"))
$PythonRecord = Join-Path $RuntimeRoot "PYTHON_EXECUTABLE"
$WindowedPythonRecord = Join-Path $RuntimeRoot "PYTHON_WINDOWED_EXECUTABLE"
$PidPath = Join-Path $RuntimeRoot "meter.pid"
$OutputLog = Join-Path $RuntimeRoot "meter.log"
$ErrorLog = Join-Path $RuntimeRoot "meter.err.log"

if ($ReadinessTimeoutSeconds -le 0) {
    $ConfiguredTimeout = 0
    if ($env:TOKEN_METER_READINESS_TIMEOUT_SECONDS) {
        [int]::TryParse($env:TOKEN_METER_READINESS_TIMEOUT_SECONDS, [ref]$ConfiguredTimeout) | Out-Null
    }
    $ReadinessTimeoutSeconds = if ($ConfiguredTimeout -gt 0) { $ConfiguredTimeout } else { 600 }
}

if (-not (Test-Path -LiteralPath $PythonRecord -PathType Leaf)) {
    throw "Token Meter's Python runtime record is missing. Reinstall Token Meter."
}
$PythonExe = (Get-Content -LiteralPath $PythonRecord -Raw).Trim()
if (Test-Path -LiteralPath $WindowedPythonRecord -PathType Leaf) {
    $WindowedPythonExe = (Get-Content -LiteralPath $WindowedPythonRecord -Raw).Trim()
    if (Test-Path -LiteralPath $WindowedPythonExe -PathType Leaf) {
        $PythonExe = $WindowedPythonExe
    }
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Token Meter's configured Python executable is unavailable. Reinstall Token Meter."
}

$Health = Get-Health
if ($Health -and -not (Test-OwnedHealth $Health $ExpectedPage)) {
    throw "A different server owns http://127.0.0.1:8722."
}

$Process = $null
if (-not $Health) {
    if (Test-Path -LiteralPath $PidPath -PathType Leaf) {
        $SavedPid = 0
        if ([int]::TryParse((Get-Content -LiteralPath $PidPath -Raw).Trim(), [ref]$SavedPid)) {
            $SavedProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $SavedPid" -ErrorAction SilentlyContinue
            if ($SavedProcess -and [string]$SavedProcess.CommandLine -like "*$MeterPath*") {
                $Process = Get-Process -Id $SavedPid -ErrorAction SilentlyContinue
            }
        }
        if (-not $Process) {
            Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
        }
    }
    if (-not $Process) {
        $PreviousUtf8 = $env:PYTHONUTF8
        $env:PYTHONUTF8 = "1"
        try {
            $Process = Start-Process -FilePath $PythonExe `
                -ArgumentList @("-X", "utf8", "`"$MeterPath`"") `
                -WorkingDirectory $RuntimeRoot `
                -RedirectStandardOutput $OutputLog `
                -RedirectStandardError $ErrorLog `
                -WindowStyle Hidden `
                -PassThru
        } finally {
            if ($null -eq $PreviousUtf8) {
                Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue
            } else {
                $env:PYTHONUTF8 = $PreviousUtf8
            }
        }
        [System.IO.File]::WriteAllText($PidPath, "$($Process.Id)`r`n", [System.Text.UTF8Encoding]::new($false))
    }
}

$Deadline = [DateTime]::UtcNow.AddSeconds($ReadinessTimeoutSeconds)
$Ready = $false
do {
    $Health = Get-Health
    if ($Health -and -not (Test-OwnedHealth $Health $ExpectedPage)) {
        throw "A different server owns http://127.0.0.1:8722."
    }
    if ($Health -and $Health.ok -and $Health.state_ready) {
        $Ready = $true
        break
    }
    if ($Process) {
        $Process.Refresh()
        if ($Process.HasExited) {
            $Detail = if (Test-Path -LiteralPath $ErrorLog) {
                (Get-Content -LiteralPath $ErrorLog -Tail 20 | Out-String).Trim()
            } else { "" }
            throw "Token Meter exited before becoming ready. $Detail"
        }
    }
    Start-Sleep -Milliseconds 500
} while ([DateTime]::UtcNow -lt $Deadline)

if (-not $Ready) {
    throw "Token Meter did not finish indexing within $ReadinessTimeoutSeconds seconds."
}
