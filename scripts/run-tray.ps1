[CmdletBinding()]
param(
    [switch]$SmokeTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
    throw "The Token Meter tray requires Windows."
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

function Limit-Text([string]$Value, [int]$Maximum) {
    $Value = ([string]$Value -replace '\s+', ' ').Trim()
    if ($Value.Length -le $Maximum) {
        return $Value
    }
    return $Value.Substring(0, [Math]::Max(0, $Maximum - 3)) + "..."
}

function Get-Value($Object, [string]$Name, $Default = $null) {
    if ($null -eq $Object) {
        return $Default
    }
    $Property = $Object.PSObject.Properties[$Name]
    if ($null -eq $Property -or $null -eq $Property.Value) {
        return $Default
    }
    return $Property.Value
}

function Format-CompactNumber($Value) {
    $Number = 0.0
    if (-not [double]::TryParse(
        [string]$Value,
        [System.Globalization.NumberStyles]::Any,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [ref]$Number
    )) {
        return "0"
    }
    if ($Number -ge 1000000) {
        return ($Number / 1000000).ToString("0.0M", [System.Globalization.CultureInfo]::InvariantCulture)
    }
    if ($Number -ge 1000) {
        return ($Number / 1000).ToString("0.0K", [System.Globalization.CultureInfo]::InvariantCulture)
    }
    return $Number.ToString("0", [System.Globalization.CultureInfo]::InvariantCulture)
}

function Format-TrayText($State) {
    if (-not $State -or -not [bool](Get-Value $State "ok" $false)) {
        return "Token Meter - waiting for the local server"
    }
    $Cost = 0.0
    [double]::TryParse(
        [string](Get-Value $State "total_cost" 0),
        [System.Globalization.NumberStyles]::Any,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [ref]$Cost
    ) | Out-Null
    $Verdict = Get-Value $State "verdict" $null
    $VerdictLabel = [string](Get-Value $Verdict "label" "Live")
    return Limit-Text (
        "Token Meter - `$$($Cost.ToString('0.00', [System.Globalization.CultureInfo]::InvariantCulture)) est - $VerdictLabel"
    ) 63
}

function Format-MenuStatus($State) {
    if (-not $State -or -not [bool](Get-Value $State "ok" $false)) {
        return "Waiting for local usage data"
    }
    $Cost = 0.0
    [double]::TryParse(
        [string](Get-Value $State "total_cost" 0),
        [System.Globalization.NumberStyles]::Any,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [ref]$Cost
    ) | Out-Null
    $Tokens = Format-CompactNumber (Get-Value $State "total_tokens" 0)
    return "`$$($Cost.ToString('0.00', [System.Globalization.CultureInfo]::InvariantCulture)) est | $Tokens tokens"
}

if ($SmokeTest) {
    $Sample = [pscustomobject]@{
        ok = $true
        total_cost = 1.25
        total_tokens = 125000
        verdict = [pscustomobject]@{ label = "Healthy" }
    }
    $Probe = New-Object System.Windows.Forms.NotifyIcon
    try {
        $Probe.Icon = [System.Drawing.SystemIcons]::Information
        $Probe.Text = Format-TrayText $Sample
        $Probe.Visible = $true
        [System.Windows.Forms.Application]::DoEvents()
        [ordered]@{
            ok = $Probe.Visible
            platform = "windows"
            widget = "NotifyIcon"
            tooltip = $Probe.Text
            menu_status = Format-MenuStatus $Sample
        } | ConvertTo-Json -Compress
    } finally {
        $Probe.Visible = $false
        $Probe.Dispose()
    }
    return
}

$RuntimeRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$PidPath = Join-Path $RuntimeRoot "tray.pid"
$StatusPath = Join-Path $RuntimeRoot "tray.status.json"
$script:BaseUrl = "http://127.0.0.1:8722"
$script:SelectedSessionId = ""
$script:LastState = $null
$script:Connected = $false

function Write-TrayStatus([bool]$Ready, [bool]$Connected) {
    $Record = [ordered]@{
        ready = $Ready
        connected = $Connected
        pid = $PID
        updated_at = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    }
    $Temporary = "$StatusPath.tmp-$PID"
    [System.IO.File]::WriteAllText(
        $Temporary,
        (($Record | ConvertTo-Json -Compress) + [Environment]::NewLine),
        [System.Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $Temporary -Destination $StatusPath -Force
}

function Open-TokenMeterUrl([string]$Url) {
    Start-Process -FilePath $Url | Out-Null
}

function Current-DashboardUrl {
    if ($script:SelectedSessionId) {
        $Escaped = [Uri]::EscapeDataString($script:SelectedSessionId)
        return "$($script:BaseUrl)/sessions/$Escaped#summary"
    }
    return "$($script:BaseUrl)/#sessions"
}

function Select-RecentSession($Sender) {
    $script:SelectedSessionId = [string]$Sender.Tag
    Invoke-TrayRefresh
}

function Follow-LatestSession {
    $script:SelectedSessionId = ""
    Invoke-TrayRefresh
}

function Update-RecentSessions($State) {
    $script:RecentMenu.DropDownItems.Clear()

    $Follow = New-Object System.Windows.Forms.ToolStripMenuItem
    $Follow.Text = "Follow latest"
    $Follow.Checked = -not [bool]$script:SelectedSessionId
    $Follow.add_Click({ Follow-LatestSession })
    $script:RecentMenu.DropDownItems.Add($Follow) | Out-Null
    $script:RecentMenu.DropDownItems.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

    $Rows = @(Get-Value $State "recent_sessions" @())
    if ($Rows.Count -eq 0) {
        $Empty = New-Object System.Windows.Forms.ToolStripMenuItem
        $Empty.Text = "No recent sessions"
        $Empty.Enabled = $false
        $script:RecentMenu.DropDownItems.Add($Empty) | Out-Null
        return
    }

    foreach ($Row in $Rows) {
        $Id = [string](Get-Value $Row "id" "")
        if (-not $Id) {
            continue
        }
        $Label = [string](Get-Value $Row "label" "Session")
        $Name = [string](Get-Value $Row "name" "Untitled")
        $Item = New-Object System.Windows.Forms.ToolStripMenuItem
        $Item.Text = Limit-Text "$Label - $Name" 80
        $Item.Tag = $Id
        $Item.Checked = $script:SelectedSessionId -eq $Id
        $Item.add_Click({ Select-RecentSession $this })
        $script:RecentMenu.DropDownItems.Add($Item) | Out-Null
    }
}

function Update-TrayMenu($State) {
    $script:NotifyIcon.Text = Format-TrayText $State
    $script:StatusItem.Text = Format-MenuStatus $State

    $Recommendation = Get-Value $State "recommendation" $null
    $RecommendationLabel = [string](Get-Value $Recommendation "label" "Waiting for guidance")
    $script:GuidanceItem.Text = Limit-Text "Guidance: $RecommendationLabel" 100

    $Activity = Get-Value $State "activity" $null
    $ActivityTitle = [string](Get-Value $Activity "title" "Waiting for activity")
    $script:ActivityItem.Text = Limit-Text "Activity: $ActivityTitle" 100

    Update-RecentSessions $State
}

function Invoke-TrayRefresh {
    try {
        $Url = "$($script:BaseUrl)/menubar"
        if ($script:SelectedSessionId) {
            $Url += "?id=$([Uri]::EscapeDataString($script:SelectedSessionId))"
        }
        $State = Invoke-RestMethod -Uri $Url -TimeoutSec 4
        $Selection = Get-Value $State "selection" $null
        if ([bool](Get-Value $Selection "missing" $false)) {
            $script:SelectedSessionId = ""
            $State = Invoke-RestMethod -Uri "$($script:BaseUrl)/menubar" -TimeoutSec 4
        }
        $script:LastState = $State
        $script:Connected = $true
        Update-TrayMenu $State
    } catch {
        $script:Connected = $false
        $script:NotifyIcon.Text = "Token Meter - local server unavailable"
        $script:StatusItem.Text = "Local server unavailable"
        $script:GuidanceItem.Text = "Guidance: reconnecting"
        $script:ActivityItem.Text = "Activity: unavailable"
    }
    Write-TrayStatus $true $script:Connected
}

$CreatedNew = $false
$Mutex = New-Object System.Threading.Mutex($true, "Local\TokenMeterTray", [ref]$CreatedNew)
if (-not $CreatedNew) {
    $Mutex.Dispose()
    exit 0
}

[System.Windows.Forms.Application]::EnableVisualStyles()
$script:NotifyIcon = New-Object System.Windows.Forms.NotifyIcon
$script:NotifyIcon.Icon = [System.Drawing.SystemIcons]::Information
$script:NotifyIcon.Text = "Token Meter - starting"
$script:NotifyIcon.Visible = $true

$Menu = New-Object System.Windows.Forms.ContextMenuStrip
$TitleItem = New-Object System.Windows.Forms.ToolStripMenuItem
$TitleItem.Text = "Token Meter"
$TitleItem.Enabled = $false
$TitleItem.Font = New-Object System.Drawing.Font($TitleItem.Font, [System.Drawing.FontStyle]::Bold)
$Menu.Items.Add($TitleItem) | Out-Null

$script:StatusItem = New-Object System.Windows.Forms.ToolStripMenuItem
$script:StatusItem.Text = "Waiting for local usage data"
$script:StatusItem.Enabled = $false
$Menu.Items.Add($script:StatusItem) | Out-Null

$script:GuidanceItem = New-Object System.Windows.Forms.ToolStripMenuItem
$script:GuidanceItem.Text = "Guidance: loading"
$script:GuidanceItem.Enabled = $false
$Menu.Items.Add($script:GuidanceItem) | Out-Null

$script:ActivityItem = New-Object System.Windows.Forms.ToolStripMenuItem
$script:ActivityItem.Text = "Activity: loading"
$script:ActivityItem.Enabled = $false
$Menu.Items.Add($script:ActivityItem) | Out-Null
$Menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

$OpenDashboard = New-Object System.Windows.Forms.ToolStripMenuItem
$OpenDashboard.Text = "Open dashboard"
$OpenDashboard.add_Click({ Open-TokenMeterUrl (Current-DashboardUrl) })
$Menu.Items.Add($OpenDashboard) | Out-Null

$OpenDaily = New-Object System.Windows.Forms.ToolStripMenuItem
$OpenDaily.Text = "Open daily brief"
$OpenDaily.add_Click({ Open-TokenMeterUrl "$($script:BaseUrl)/#daily" })
$Menu.Items.Add($OpenDaily) | Out-Null

$OpenBudgets = New-Object System.Windows.Forms.ToolStripMenuItem
$OpenBudgets.Text = "Open monthly budgets"
$OpenBudgets.add_Click({ Open-TokenMeterUrl "$($script:BaseUrl)/#settings-budgets" })
$Menu.Items.Add($OpenBudgets) | Out-Null

$script:RecentMenu = New-Object System.Windows.Forms.ToolStripMenuItem
$script:RecentMenu.Text = "Recent sessions"
$Menu.Items.Add($script:RecentMenu) | Out-Null
$Menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

$Refresh = New-Object System.Windows.Forms.ToolStripMenuItem
$Refresh.Text = "Refresh now"
$Refresh.add_Click({ Invoke-TrayRefresh })
$Menu.Items.Add($Refresh) | Out-Null

$Quit = New-Object System.Windows.Forms.ToolStripMenuItem
$Quit.Text = "Quit tray widget"
$Quit.add_Click({ $script:Context.ExitThread() })
$Menu.Items.Add($Quit) | Out-Null

$script:NotifyIcon.ContextMenuStrip = $Menu
$script:NotifyIcon.add_DoubleClick({ Open-TokenMeterUrl (Current-DashboardUrl) })
$Menu.add_Opening({ Invoke-TrayRefresh })

$Timer = New-Object System.Windows.Forms.Timer
$Timer.Interval = 10000
$Timer.add_Tick({ Invoke-TrayRefresh })
$script:Context = New-Object System.Windows.Forms.ApplicationContext

try {
    [System.IO.File]::WriteAllText($PidPath, "$PID`r`n", [System.Text.UTF8Encoding]::new($false))
    Invoke-TrayRefresh
    $Timer.Start()
    [System.Windows.Forms.Application]::Run($script:Context)
} finally {
    $Timer.Stop()
    $Timer.Dispose()
    $script:NotifyIcon.Visible = $false
    $script:NotifyIcon.Dispose()
    $Menu.Dispose()
    $script:Context.Dispose()
    foreach ($Path in @($PidPath, $StatusPath)) {
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            $SavedPid = 0
            $Value = if ($Path -eq $PidPath) {
                (Get-Content -LiteralPath $Path -Raw).Trim()
            } else {
                try { [string](Get-Value (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json) "pid" 0) } catch { "0" }
            }
            if ([int]::TryParse($Value, [ref]$SavedPid) -and $SavedPid -eq $PID) {
                Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
            }
        }
    }
    $Mutex.ReleaseMutex()
    $Mutex.Dispose()
}
