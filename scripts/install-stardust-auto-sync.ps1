param(
    [ValidateRange(1, 1440)]
    [int]$Minutes = 5
)

$ErrorActionPreference = 'Stop'
$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA 'Hermes' }
$ScriptPath = Join-Path $HermesHome 'hermes-agent\scripts\stardust-auto-sync.ps1'
$TaskName = 'Stardust Auto Sync'

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    throw "Auto-sync script not found: $ScriptPath"
}

$PowerShell = (Get-Command powershell.exe).Source
$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$ScriptPath`""
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $Minutes)
$Settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description 'Safely fast-forward Stardust from 9529360-cpu/stardust-hermes main, validate, package, and restart.' -Force | Out-Null
Write-Output "Installed '$TaskName' (every $Minutes minutes)."
Write-Output "Script: $ScriptPath"
