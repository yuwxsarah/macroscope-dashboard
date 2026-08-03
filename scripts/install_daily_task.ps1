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

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument ('/d /c "' + $runner + '"') `
    -WorkingDirectory $resolvedRoot
$trigger = New-ScheduledTaskTrigger -Daily -At "19:20"
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName "MacroScope Daily Data Refresh" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Refresh MacroScope public market data and rebuild the local dashboard every day." `
    -Force | Out-Null
