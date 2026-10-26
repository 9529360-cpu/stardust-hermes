$ErrorActionPreference = "Continue"
$Repo = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$Root = if ($env:STARDUST_BACKEND_LAB_ARTIFACTS) { $env:STARDUST_BACKEND_LAB_ARTIFACTS } else { Join-Path $Repo "Reports\backend-lab" }
New-Item -ItemType Directory -Force -Path $Root | Out-Null
while ($true) {
  & $Python -m http.server 8765 --bind 127.0.0.1 --directory $Root
  Start-Sleep -Seconds 2
}
