$ErrorActionPreference = "Continue"
$Repo = Split-Path -Parent $PSScriptRoot
$Workspace = Split-Path -Parent $Repo
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$Root = Join-Path $Workspace "artifacts\backend-lab"
New-Item -ItemType Directory -Force -Path $Root | Out-Null
while ($true) {
  & $Python -m http.server 8765 --bind 127.0.0.1 --directory $Root
  Start-Sleep -Seconds 2
}
