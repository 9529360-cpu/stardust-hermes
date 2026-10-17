# Process-level tests for scripts/install-stardust.ps1.
#
# The child PowerShell process gets a local Invoke-WebRequest function that
# returns a fixture. The test entrypoint appends the current wrapper source so
# it runs at process top level and exposes its exit code. No network request or
# product installation is possible.

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$wrapper = Join-Path $repoRoot 'scripts/install-stardust.ps1'
$powerShellExe = (Get-Process -Id $PID).Path
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("stardust-wrapper-tests-{0}" -f [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempRoot | Out-Null

$harnessPath = Join-Path $tempRoot 'invoke-wrapper.ps1'
$harnessPrefix = @'
$ErrorActionPreference = 'Stop'
function Invoke-WebRequest {
    param([string]$Uri, [switch]$UseBasicParsing)
    [System.IO.File]::WriteAllText($env:STARDUST_URL_CAPTURE, $Uri)
    if ($env:STARDUST_FAIL_DOWNLOAD -eq '1') { throw 'simulated download failure' }
    return [pscustomobject]@{ Content = [System.IO.File]::ReadAllText($env:STARDUST_FAKE_SOURCE) }
}
$args = [string[]](ConvertFrom-Json -InputObject $env:STARDUST_TEST_ARGS)
if ([int]$env:STARDUST_SEED_EXIT_CODE -ne 0) {
    & $env:ComSpec /c ("exit " + $env:STARDUST_SEED_EXIT_CODE)
}
'@
$wrapperSource = [System.IO.File]::ReadAllText($wrapper, [System.Text.Encoding]::UTF8)
$harness = $harnessPrefix + [System.Environment]::NewLine + $wrapperSource
[System.IO.File]::WriteAllText($harnessPath, $harness, [System.Text.UTF8Encoding]::new($false))

$rewritableSource = @'
$references = @(
    'git@github.com:NousResearch/hermes-agent'
    'https://github.com/NousResearch/hermes-agent/archive/main.zip'
    'https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.ps1'
    'https://hermes-agent.nousresearch.com/install.ps1'
    'https://hermes-agent.nousresearch.com/install.sh'
    'Hermes Agent Installer'
    'An open source AI agent by Nous Research.'
    'Download Hermes Agent'
)
[System.IO.File]::WriteAllLines($env:STARDUST_CONTENT_CAPTURE, [string[]]$references)
[System.IO.File]::WriteAllLines($env:STARDUST_INSTALL_CAPTURE, [string[]]$args)
'@

$expectedRewrittenSource = @(
    'git@github.com:9529360-cpu/stardust-hermes'
    'https://github.com/9529360-cpu/stardust-hermes/archive/main.zip'
    'https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install.ps1'
    'https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1'
    'https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh'
    'Stardust Personal Assistant Installer'
    'Personal assistant built on the Hermes Agent foundation.'
    'Download Stardust'
)

$leakingSource = @'
[System.IO.File]::WriteAllText($env:STARDUST_INSTALL_CAPTURE, 'installer ran')
$unused = 'git://github.com/NousResearch/hermes-agent'
$global:LASTEXITCODE = 0
'@

function Assert-Equal {
    param($Expected, $Actual, [Parameter(Mandatory = $true)][string]$Label)
    if ($Expected -cne $Actual) {
        throw "FAIL: $Label`n  expected: $Expected`n  actual:   $Actual"
    }
    Write-Host "OK: $Label" -ForegroundColor Green
}

function Assert-Sequence {
    param([string[]]$Expected, [string[]]$Actual, [Parameter(Mandatory = $true)][string]$Label)
    if (($Expected -join "`n") -cne ($Actual -join "`n")) {
        throw "FAIL: $Label`n  expected: [$($Expected -join ', ')]`n  actual:   [$($Actual -join ', ')]"
    }
    Write-Host "OK: $Label" -ForegroundColor Green
}

function Invoke-WrapperProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Source,
        [string[]]$Arguments = @(),
        [AllowNull()][string]$InstallRef = $null,
        [switch]$FailDownload,
        [int]$SeedExitCode = 0
    )

    $prefix = Join-Path $tempRoot $Name
    $sourcePath = "$prefix-source.ps1"
    $urlCapture = "$prefix-url.txt"
    $contentCapture = "$prefix-content.txt"
    $installCapture = "$prefix-args.txt"
    $stdoutPath = "$prefix-stdout.txt"
    $stderrPath = "$prefix-stderr.txt"
    [System.IO.File]::WriteAllText($sourcePath, $Source, [System.Text.UTF8Encoding]::new($false))

    $processInfo = New-Object System.Diagnostics.ProcessStartInfo
    $processInfo.FileName = $powerShellExe
    $processInfo.Arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f $harnessPath
    $processInfo.WorkingDirectory = $repoRoot
    $processInfo.UseShellExecute = $false
    $processInfo.CreateNoWindow = $true
    $processInfo.RedirectStandardOutput = $true
    $processInfo.RedirectStandardError = $true
    $failDownloadValue = if ($FailDownload) { '1' } else { '0' }
    $environmentValues = @{
        STARDUST_WRAPPER_PATH = $wrapper
        STARDUST_FAKE_SOURCE = $sourcePath
        STARDUST_URL_CAPTURE = $urlCapture
        STARDUST_CONTENT_CAPTURE = $contentCapture
        STARDUST_INSTALL_CAPTURE = $installCapture
        STARDUST_FAIL_DOWNLOAD = $failDownloadValue
        STARDUST_SEED_EXIT_CODE = [string]$SeedExitCode
        STARDUST_TEST_ARGS = if ($Arguments.Count) {
        ConvertTo-Json -InputObject $Arguments -Compress
        } else {
            '[]'
        }
    }
    $environmentKeys = @($environmentValues.Keys) + @('STARDUST_INSTALL_REF')
    $savedEnvironment = @{}
    foreach ($key in $environmentKeys) { $savedEnvironment[$key] = [Environment]::GetEnvironmentVariable($key, 'Process') }

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $processInfo
    try {
        foreach ($key in $environmentValues.Keys) { Set-Item -Path "Env:$key" -Value $environmentValues[$key] }
        if ($null -ne $InstallRef) {
            Set-Item -Path 'Env:STARDUST_INSTALL_REF' -Value $InstallRef
        } else {
            Remove-Item -Path 'Env:STARDUST_INSTALL_REF' -ErrorAction SilentlyContinue
        }
        if (-not $process.Start()) { throw "Could not start PowerShell for scenario '$Name'" }
        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        [System.IO.File]::WriteAllText($stdoutPath, $stdout)
        [System.IO.File]::WriteAllText($stderrPath, $stderr)
    } finally {
        foreach ($key in $environmentKeys) {
            if ($null -eq $savedEnvironment[$key]) {
                Remove-Item -Path "Env:$key" -ErrorAction SilentlyContinue
            } else {
                Set-Item -Path "Env:$key" -Value $savedEnvironment[$key]
            }
        }
    }

    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        UrlCapture = $urlCapture
        ContentCapture = $contentCapture
        InstallCapture = $installCapture
        Output = "$stdout`n$stderr"
    }
}

try {
    if (-not (Test-Path -LiteralPath $wrapper -PathType Leaf)) { throw "Missing wrapper: $wrapper" }

    $default = Invoke-WrapperProcess -Name 'default' -Source $rewritableSource
    Assert-Equal 0 $default.ExitCode 'default wrapper fixture completes successfully'
    Assert-Equal 'https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install.ps1' (Get-Content -Raw -LiteralPath $default.UrlCapture) 'default install source is Stardust main'
    Assert-Sequence $expectedRewrittenSource @(Get-Content -LiteralPath $default.ContentCapture) 'source and recovery URLs plus installer copy are rewritten before execution'
    Assert-Sequence @() @(Get-Content -LiteralPath $default.InstallCapture -ErrorAction SilentlyContinue) 'no arguments remain empty'

    $nonzeroExitSource = $rewritableSource.Replace('$global:LASTEXITCODE = 0', '$null = $null')
    $nonzeroExit = Invoke-WrapperProcess -Name 'installer-nonzero-exit' -Source $nonzeroExitSource -SeedExitCode 23
    Assert-Equal 23 $nonzeroExit.ExitCode 'nonzero installer exit code is preserved'

    $commitArgs = @('-Manifest', 'manifest.json', '-Stage', '3', '-NonInteractive', '-Json', '-IncludeDesktop', '-Commit', 'commit-sha', '-Tag', 'v1.2.3', '-Branch', 'feature/test')
    $commit = Invoke-WrapperProcess -Name 'commit' -Source $rewritableSource -Arguments $commitArgs
    Assert-Equal 0 $commit.ExitCode 'commit-pinned wrapper fixture completes successfully'
    Assert-Equal 'https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/commit-sha/scripts/install.ps1' (Get-Content -Raw -LiteralPath $commit.UrlCapture) 'commit pin takes precedence over tag and branch'
    Assert-Sequence $commitArgs @(Get-Content -LiteralPath $commit.InstallCapture) 'PowerShell wrapper forwards every option and value unchanged'

    $tagArgs = @('-Tag', 'v2.4.0', '-Branch', 'ignored-branch')
    $tag = Invoke-WrapperProcess -Name 'tag' -Source $rewritableSource -Arguments $tagArgs
    Assert-Equal 'https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/v2.4.0/scripts/install.ps1' (Get-Content -Raw -LiteralPath $tag.UrlCapture) 'tag pin takes precedence over branch'
    Assert-Sequence $tagArgs @(Get-Content -LiteralPath $tag.InstallCapture) 'tag and branch arguments are forwarded'

    $branchArgs = @('-Branch', 'release-candidate')
    $branch = Invoke-WrapperProcess -Name 'branch' -Source $rewritableSource -Arguments $branchArgs
    Assert-Equal 'https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/release-candidate/scripts/install.ps1' (Get-Content -Raw -LiteralPath $branch.UrlCapture) 'branch ref selects the downloaded installer'
    Assert-Sequence $branchArgs @(Get-Content -LiteralPath $branch.InstallCapture) 'branch argument is forwarded'

    $overrideArgs = @('-Commit', 'argument-pin', '-NonInteractive')
    $override = Invoke-WrapperProcess -Name 'override' -Source $rewritableSource -Arguments $overrideArgs -InstallRef 'environment-pin'
    Assert-Equal 'https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/environment-pin/scripts/install.ps1' (Get-Content -Raw -LiteralPath $override.UrlCapture) 'STARDUST_INSTALL_REF overrides the argument pin'
    Assert-Sequence $overrideArgs @(Get-Content -LiteralPath $override.InstallCapture) 'environment override does not alter forwarded options'

    $leak = Invoke-WrapperProcess -Name 'leak' -Source $leakingSource
    if ($leak.ExitCode -eq 0) { throw 'FAIL: surviving upstream product-source URL must fail closed' }
    if (Test-Path -LiteralPath $leak.InstallCapture) { throw 'FAIL: wrapper executed installer content after URL enforcement failed' }
    if ($leak.Output -notmatch 'upstream product-source URL survived rewriting') { throw "FAIL: expected fail-closed diagnostic was missing`n$($leak.Output)" }
    Write-Host 'OK: surviving upstream product-source URL blocks execution' -ForegroundColor Green

    $downloadFailure = Invoke-WrapperProcess -Name 'download-failure' -Source $rewritableSource -FailDownload
    if ($downloadFailure.ExitCode -eq 0) { throw 'FAIL: failed PowerShell download must exit nonzero' }
    if (Test-Path -LiteralPath $downloadFailure.InstallCapture) { throw 'FAIL: installer ran after PowerShell download failed' }
    if ($downloadFailure.Output -notmatch 'simulated download failure') { throw "FAIL: expected download failure diagnostic was missing`n$($downloadFailure.Output)" }
    Write-Host 'OK: failed PowerShell download stops before installer execution' -ForegroundColor Green

    Write-Host ''
    Write-Host 'all Stardust PowerShell wrapper assertions passed'
} finally {
    $tempPath = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    $fullTempRoot = [System.IO.Path]::GetFullPath($tempRoot)
    if ($fullTempRoot.StartsWith($tempPath, [System.StringComparison]::OrdinalIgnoreCase) -and (Split-Path -Leaf $fullTempRoot).StartsWith('stardust-wrapper-tests-', [System.StringComparison]::Ordinal)) {
        Remove-Item -LiteralPath $fullTempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
