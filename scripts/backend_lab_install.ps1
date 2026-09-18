$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$Watch = Join-Path $PSScriptRoot "backend_lab_watch.ps1"
$Deep = Join-Path $PSScriptRoot "backend_lab_deep.ps1"
$Dashboard = Join-Path $PSScriptRoot "backend_lab_dashboard.ps1"
$Allure = Join-Path $PSScriptRoot "backend_lab_allure.ps1"

function Register-LabTask([string]$Name, [string]$Schedule, [string]$Script, [string[]]$Extra) {
  $taskCommand = "$PowerShell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Script`""
  $args = @("/Create", "/TN", $Name, "/SC", $Schedule)
  $args += $Extra
  $args += @("/TR", $taskCommand, "/F")
  & schtasks.exe @args | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "Failed to register $Name" }
}

Register-LabTask "StardustBackendLabWatch" "ONLOGON" $Watch @()
Register-LabTask "StardustBackendLabDashboard" "ONLOGON" $Dashboard @()
Register-LabTask "StardustBackendLabAllure" "ONLOGON" $Allure @()
Register-LabTask "StardustBackendLabDeep" "MINUTE" $Deep @("/MO", "30")

$Forever = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -MultipleInstances IgnoreNew
Set-ScheduledTask -TaskName "StardustBackendLabWatch" -Settings $Forever | Out-Null
Set-ScheduledTask -TaskName "StardustBackendLabDashboard" -Settings $Forever | Out-Null
Set-ScheduledTask -TaskName "StardustBackendLabAllure" -Settings $Forever | Out-Null
$DeepSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2) -StartWhenAvailable -MultipleInstances IgnoreNew
Set-ScheduledTask -TaskName "StardustBackendLabDeep" -Settings $DeepSettings | Out-Null

try {
  $rule = "Stardust Backend Lab Allure Remote Block"
  Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
  New-NetFirewallRule -DisplayName $rule -Direction Inbound -Action Block -Protocol TCP -LocalPort 8766 -Profile Any | Out-Null
} catch {
  Write-Warning "Could not refresh the Allure firewall rule: $($_.Exception.Message)"
}

foreach ($name in @("StardustBackendLabWatch", "StardustBackendLabDashboard", "StardustBackendLabAllure", "StardustBackendLabDeep")) {
  & schtasks.exe /Run /TN $name | Out-Host
}
Write-Output "Backend Lab scheduled tasks installed and started."
