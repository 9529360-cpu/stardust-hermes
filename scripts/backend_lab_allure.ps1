$ErrorActionPreference = "Continue"
$Repo = Split-Path -Parent $PSScriptRoot
$Workspace = Split-Path -Parent $Repo
$Node = "D:\DevTools\Node26\node.exe"
$Allure = "D:\DevTools\Allure3\node_modules\allure\cli.js"
$Root = Join-Path $Workspace "artifacts\backend-lab"
$Results = Join-Path $Root "allure-results"
$AllureRoot = Join-Path $Root "allure"
$LiveConfig = Join-Path $AllureRoot "allurerc-live.json"
$LiveHistory = Join-Path $AllureRoot "live-history.jsonl"
$Live = Join-Path $AllureRoot "live"
$Port = 8766
New-Item -ItemType Directory -Force -Path $Results,$AllureRoot | Out-Null

# Task Scheduler can stop the PowerShell wrapper without reaping its Node child.
# Retire only a listener whose command line proves it is our Allure watch.
foreach ($listener in @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) {
  $ownerPid = [int]$listener.OwningProcess
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerPid" -ErrorAction SilentlyContinue
  $cmd = [string]$proc.CommandLine
  if ($cmd -and $cmd -like "*allure*cli.js*" -and $cmd -match "(?i)\bwatch\b" -and $cmd -match "(?i)--port\s+$Port(?:\s|$)") {
    Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 250
  }
}

# The persistent watch process must not share the durable history file with
# one-shot generate runs: concurrent Allure 3 FileHandle ownership can make
# Node 26 fail report generation during history compaction/close.
$cfg = @{ name="Stardust Backend Lab Live"; historyPath=($LiveHistory -replace "\\","/"); appendHistory=$true; historyLimit=200 } | ConvertTo-Json
$utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($LiveConfig, $cfg, $utf8)
while ($true) {
  & $Node $Allure watch $Results --config $LiveConfig --output $Live --report-name "Stardust Backend Lab Live" --port 8766 --no-new-only --preserve
  Start-Sleep -Seconds 2
}
