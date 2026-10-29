$ErrorActionPreference = 'Stop'
$desktop = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$dist = 'D:\Development\Temp\Stardust\task-center-owner-route-20261029\wip-review-20261029\project-cwd-electron\dist'
$report = 'D:\Development\Reports\Stardust\task-center-owner-route-20261029\wip-review-20261029'
$env:STARDUST_DESKTOP_DIST_DIR = $dist
$env:VITE_PERF_PROBE = '1'
$env:TEMP = 'D:\Development\Temp\Stardust\task-center-owner-route-20261029\wip-review-20261029\temp\build'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
$isolatedEsbuild = Join-Path $env:TEMP 'esbuild.exe'
$sourceEsbuild = 'D:\项目\Stardust-Jarvis\apps\desktop\node_modules\.pnpm\@esbuild+win32-x64@0.28.1\node_modules\@esbuild\win32-x64\esbuild.exe'
if (!(Test-Path -LiteralPath $sourceEsbuild -PathType Leaf)) { throw "Installed Windows esbuild binary is missing: $sourceEsbuild" }
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
Copy-Item -LiteralPath $sourceEsbuild -Destination $isolatedEsbuild -Force
$env:ESBUILD_BINARY_PATH = $isolatedEsbuild
New-Item -ItemType Directory -Path $dist -Force | Out-Null
New-Item -ItemType Directory -Path $report -Force | Out-Null
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
Push-Location $desktop
try {
  node scripts/assert-root-install.mjs
  if ($LASTEXITCODE -ne 0) { throw "root install assertion failed: $LASTEXITCODE" }
  $rendererLog = Join-Path $report 'renderer-build.log'
  $previousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    npm exec --workspace stardust-desktop -- vite build *> $rendererLog
    $rendererExitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
  if ($rendererExitCode -ne 0) {
    Get-Content -LiteralPath $rendererLog -Tail 100
    throw "renderer build failed: $rendererExitCode (log: $rendererLog)"
  }
  node scripts/bundle-electron-main.mjs
  if ($LASTEXITCODE -ne 0) { throw "electron bundle failed: $LASTEXITCODE" }
  node scripts/stage-native-deps.mjs
  if ($LASTEXITCODE -ne 0) { throw "native staging failed: $LASTEXITCODE" }
  node scripts/stage-browser-bridge.mjs
  if ($LASTEXITCODE -ne 0) { throw "browser bridge staging failed: $LASTEXITCODE" }
  $names = @('index.html', 'electron-main.mjs', 'electron-preload.js')
  $hashes = @{}
  foreach ($name in $names) {
    $path = Join-Path $dist $name
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "missing isolated candidate artifact: $path" }
    $hashes[$name] = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash
  }
  $hashes | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $report 'isolated-build-hashes.json')
} finally {
  Pop-Location
}
