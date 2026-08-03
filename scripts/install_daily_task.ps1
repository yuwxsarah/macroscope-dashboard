param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot
)

$ErrorActionPreference = "Stop"
$resolvedRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$runner = Join-Path $resolvedRoot "RUN_DAILY_UPDATE_WINDOWS.bat"

if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "Daily update runner not found: $runner"
}

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

function Register-MacroScopeTask {
    param(
        [string]$TaskName,
        [string]$At,
        [string]$Mode,
        [string]$Description
    )

    $action = New-ScheduledTaskAction `
        -Execute "cmd.exe" `
        -Argument ('/d /c ""' + $runner + '" --refresh-mode ' + $Mode + '"') `
        -WorkingDirectory $resolvedRoot
    $trigger = New-ScheduledTaskTrigger `
        -Weekly `
        -WeeksInterval 1 `
        -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
        -At $At

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description $Description `
        -Force | Out-Null
}

Register-MacroScopeTask `
    -TaskName "MacroScope Midday Snapshot" `
    -At "11:40" `
    -Mode "snapshot" `
    -Description "Capture a lightweight A-share midday snapshot and rebuild MacroScope."

Register-MacroScopeTask `
    -TaskName "MacroScope Close Data Refresh" `
    -At "15:20" `
    -Mode "close" `
    -Description "Capture A-share closing data and rebuild MacroScope after market close."

Register-MacroScopeTask `
    -TaskName "MacroScope Close Data Retry" `
    -At "16:40" `
    -Mode "close" `
    -Description "Retry A-share closing data after public sources have settled."

Register-MacroScopeTask `
    -TaskName "MacroScope Daily Data Refresh" `
    -At "19:20" `
    -Mode "all" `
    -Description "Refresh all MacroScope public datasets and rebuild the local dashboard."
