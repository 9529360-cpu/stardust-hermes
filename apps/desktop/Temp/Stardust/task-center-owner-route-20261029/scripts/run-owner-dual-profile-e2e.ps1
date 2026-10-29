$ErrorActionPreference = 'Stop'
$desktop = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$dist = 'D:\Development\Temp\Stardust\task-center-owner-route-20261029\wip-review-20261029\project-cwd-electron\dist'
$report = 'D:\Development\Reports\Stardust\task-center-owner-route-20261029\wip-review-20261029'
$temp = 'D:\Development\Temp\Stardust\task-center-owner-route-20261029\wip-review-20261029\temp\e2e'
$runtime = 'D:\Development\Temp\Stardust\stream-stall-diagnosis\electron-dist'
$python = 'D:\项目\Stardust-Jarvis\.venv\Scripts\python.exe'
$repoRoot = (Resolve-Path (Join-Path $desktop '..\..')).Path
New-Item -ItemType Directory -Path $temp -Force | Out-Null
New-Item -ItemType Directory -Path $report -Force | Out-Null
$env:STARDUST_DESKTOP_DIST_DIR = $dist
$env:STARDUST_DESKTOP_E2E_TASK_ROOT = 'D:\Development\Temp\Stardust\task-center-owner-route-20261029\wip-review-20261029'
$env:STARDUST_DESKTOP_E2E_REPORT_DIR = $report
$env:STARDUST_DESKTOP_E2E_TEMP_ROOT = $temp
$env:STARDUST_DESKTOP_USER_DATA_DIR = Join-Path $temp 'runner-userdata'
$env:STARDUST_DESKTOP_PYTHON = $python
$env:STARDUST_DESKTOP_HERMES_ROOT = $repoRoot
$env:STARDUST_DESKTOP_SKIP_INTRO = '1'
$env:STARDUST_DESKTOP_IGNORE_EXISTING = '1'
$env:STARDUST_DESKTOP_APP_NAME = 'StardustOwnerDualProfileIsolated'
$env:ELECTRON_OVERRIDE_DIST_PATH = $runtime
$env:TEMP = $temp
$env:TMP = $temp
$env:TMPDIR = $temp
Push-Location $desktop
try {
  npm exec --workspace stardust-desktop -- playwright test e2e/task-center-owner-routing.spec.ts --workers=1 --reporter=line
  if ($LASTEXITCODE -ne 0) { throw "dual-profile native acceptance failed: $LASTEXITCODE" }
} finally {
  Pop-Location
}
