# Stardust-owned Windows install entrypoint.
#
# Downloads the mature Hermes PowerShell installer from this repository, then
# rewrites only the source-authority / recovery URLs before executing it. This
# keeps the private product pinned to 9529360-cpu/stardust-hermes without
# duplicating the large Windows installer or its repair logic.

$ErrorActionPreference = 'Stop'

$Repo = '9529360-cpu/stardust-hermes'
$Ref = if ($env:STARDUST_INSTALL_REF) { $env:STARDUST_INSTALL_REF } else { 'main' }
$InstallerUrl = "https://raw.githubusercontent.com/$Repo/$Ref/scripts/install.ps1"
$TempInstaller = Join-Path ([System.IO.Path]::GetTempPath()) ("stardust-install-{0}.ps1" -f ([guid]::NewGuid().ToString('N')))

try {
    $Source = (Invoke-WebRequest -UseBasicParsing -Uri $InstallerUrl).Content
    $Source = $Source.Replace('git@github.com:NousResearch/hermes-agent.git', 'git@github.com:9529360-cpu/stardust-hermes.git')
    $Source = $Source.Replace('https://github.com/NousResearch/hermes-agent.git', 'https://github.com/9529360-cpu/stardust-hermes.git')
    $Source = $Source.Replace('https://hermes-agent.nousresearch.com/install.ps1', 'https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1')
    $Source = $Source.Replace('https://hermes-agent.nousresearch.com/install.sh', 'https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh')

    [System.IO.File]::WriteAllText($TempInstaller, $Source, [System.Text.UTF8Encoding]::new($false))

    & $TempInstaller @args
    exit $LASTEXITCODE
}
finally {
    Remove-Item -LiteralPath $TempInstaller -Force -ErrorAction SilentlyContinue
}
