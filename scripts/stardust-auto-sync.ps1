param(
    [switch]$CheckOnly,
    [switch]$NoRestart
)

$ErrorActionPreference = 'Stop'
$Repo = '9529360-cpu/stardust-hermes'
$ExpectedOrigin = "https://github.com/$Repo.git"
$HermesHome = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:LOCALAPPDATA 'Hermes' }
$Checkout = Join-Path $HermesHome 'hermes-agent'
$Desktop = Join-Path $Checkout 'apps\desktop'
$Release = Join-Path $Desktop 'release\win-unpacked'
$BackupRelease = Join-Path $Desktop 'release\.stardust-last-good-win-unpacked'
$LogDir = Join-Path $HermesHome 'logs'
$LogFile = Join-Path $LogDir 'stardust-auto-sync.log'
$StateFile = Join-Path $HermesHome 'stardust-auto-sync.json'
$Mutex = [Threading.Mutex]::new($false, 'Local\StardustAutoSync')
$HasLock = $false

function Write-Log([string]$Message) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    Write-Output $line
}

function Invoke-Step([string]$File, [string[]]$Arguments, [string]$WorkingDirectory) {
    Write-Log ("RUN {0} {1}" -f $File, ($Arguments -join ' '))
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -NoNewWindow -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "$File exited with code $($process.ExitCode)"
    }
}

function Git([string[]]$Arguments) {
    $output = & git -C $Checkout @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed: $($output -join [Environment]::NewLine)"
    }
    return ($output -join "`n").Trim()
}

function Save-State([string]$Status, [string]$Current, [string]$Target, [string]$Message) {
    $state = [ordered]@{
        checkedAt = (Get-Date).ToUniversalTime().ToString('o')
        status = $Status
        current = $Current
        target = $Target
        message = $Message
    }
    $state | ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8
}

function Start-Stardust {
    $exe = Join-Path $Release 'Hermes.exe'
    if ((Test-Path -LiteralPath $exe) -and -not $NoRestart) {
        Start-Process -FilePath $exe | Out-Null
        Write-Log 'Started Stardust desktop.'
    }
}

try {
    $HasLock = $Mutex.WaitOne(0)
    if (-not $HasLock) {
        Write-Log 'Another sync is already running; exiting.'
        exit 0
    }

    if (-not (Test-Path -LiteralPath (Join-Path $Checkout '.git'))) {
        throw "Checkout not found: $Checkout"
    }

    $origin = Git @('remote', 'get-url', 'origin')
    $canonicalOrigin = $origin.TrimEnd('/').TrimEnd('.git')
    $canonicalExpected = $ExpectedOrigin.TrimEnd('/').TrimEnd('.git')
    if ($canonicalOrigin -ne $canonicalExpected) {
        throw "Refusing untrusted origin: $origin"
    }

    $dirty = Git @('status', '--porcelain')
    if ($dirty) {
        Save-State 'deferred' '' '' 'Tracked local changes are present.'
        Write-Log 'Tracked local changes are present; leaving the installation untouched.'
        exit 0
    }

    Git @('fetch', '--no-tags', 'origin', '+refs/heads/main:refs/remotes/origin/main') | Out-Null
    $current = Git @('rev-parse', 'HEAD')
    $target = Git @('rev-parse', 'refs/remotes/origin/main')

    if ($current -eq $target) {
        Save-State 'current' $current $target 'Already current.'
        Write-Log "Already current at $($current.Substring(0, 12))."
        exit 0
    }

    & git -C $Checkout merge-base --is-ancestor $current $target
    if ($LASTEXITCODE -ne 0) {
        Save-State 'refused' $current $target 'Remote main is not a fast-forward descendant.'
        throw 'Remote main diverged or moved backwards; refusing automatic update.'
    }

    if ($CheckOnly) {
        Save-State 'available' $current $target 'Fast-forward update available.'
        Write-Log "Update available: $($current.Substring(0, 12)) -> $($target.Substring(0, 12))."
        exit 10
    }

    Write-Log "Applying verified fast-forward $($current.Substring(0, 12)) -> $($target.Substring(0, 12))."
    Git @('update-ref', 'refs/stardust/auto-sync-last-good', $current) | Out-Null
    Git @('merge', '--ff-only', 'refs/remotes/origin/main') | Out-Null

    $dependencyChanges = Git @('diff', '--name-only', $current, $target, '--', 'package.json', 'package-lock.json', 'apps/desktop/package.json')
    if ($dependencyChanges) {
        Invoke-Step 'npm.cmd' @('install') $Checkout
    }

    Invoke-Step 'npm.cmd' @('run', 'typecheck') $Desktop
    Invoke-Step 'npx.cmd' @('vitest', 'run', '--project', 'ui', 'src/reference-shell.test.ts', 'src/app/contrib/personal-layout.test.ts', 'src/store/statusbar-prefs.test.ts') $Desktop
    Invoke-Step 'npm.cmd' @('run', 'build') $Desktop

    if (-not $NoRestart) {
        Get-Process -Name Hermes -ErrorAction SilentlyContinue | Stop-Process -Force
        Start-Sleep -Seconds 2
    }

    if (Test-Path -LiteralPath $BackupRelease) {
        Remove-Item -LiteralPath $BackupRelease -Recurse -Force
    }
    if (Test-Path -LiteralPath $Release) {
        Move-Item -LiteralPath $Release -Destination $BackupRelease
    }

    try {
        Invoke-Step 'npm.cmd' @('run', 'builder', '--', '--dir', '--publish', 'never') $Desktop
        if (-not (Test-Path -LiteralPath (Join-Path $Release 'Hermes.exe'))) {
            throw 'Packager completed without producing Hermes.exe.'
        }
        if (Test-Path -LiteralPath $BackupRelease) {
            Remove-Item -LiteralPath $BackupRelease -Recurse -Force
        }
    }
    catch {
        if (Test-Path -LiteralPath $Release) {
            Remove-Item -LiteralPath $Release -Recurse -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $BackupRelease) {
            Move-Item -LiteralPath $BackupRelease -Destination $Release
        }
        throw
    }

    Save-State 'updated' $current $target 'Validated, packaged, and activated.'
    Write-Log "Update complete at $($target.Substring(0, 12))."
    Start-Stardust
    exit 0
}
catch {
    $message = $_.Exception.Message
    Write-Log "FAILED: $message"

    try {
        if ($current -and $target -and $current -ne $target) {
            Git @('reset', '--hard', $current) | Out-Null
            Write-Log "Source rolled back to $($current.Substring(0, 12))."
        }
        if ((Test-Path -LiteralPath $BackupRelease) -and -not (Test-Path -LiteralPath $Release)) {
            Move-Item -LiteralPath $BackupRelease -Destination $Release
        }
        Save-State 'failed' $current $target $message
        Start-Stardust
    }
    catch {
        Write-Log "Rollback warning: $($_.Exception.Message)"
    }
    exit 1
}
finally {
    if ($HasLock) { $Mutex.ReleaseMutex() }
    $Mutex.Dispose()
}
