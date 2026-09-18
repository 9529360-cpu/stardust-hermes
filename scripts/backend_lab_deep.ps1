$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$Lab = Join-Path $Repo "scripts\backend_lab.py"
Set-Location -LiteralPath $Repo
& $Python $Lab deep
exit $LASTEXITCODE
