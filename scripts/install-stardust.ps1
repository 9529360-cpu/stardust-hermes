# Stardust-owned Windows install entrypoint.
#
# Downloads the mature PowerShell installer from this repository, rewrites every
# product-source / recovery URL to Stardust, then refuses to execute if an
# upstream Hermes source URL survived. This keeps repair and fallback paths from
# silently replacing the personal build with NousResearch/hermes-agent.
# User-facing inherited installer copy is normalized to Stardust; internal
# Hermes paths/commands remain where compatibility still requires them.
#
# STARDUST_INSTALL_REF can override the source script ref explicitly. Otherwise
# inherit -Commit / -Tag / -Branch from a stage-protocol invocation so packaged
# desktop/bootstrap installs execute installer logic from the same pinned ref.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Get-StardustInstallRef([object[]]$Arguments) {
    $CommitRef = ''
    $TagRef = ''
    $BranchRef = ''

    for ($i = 0; $i -lt $Arguments.Count; $i++) {
        $Arg = [string]$Arguments[$i]

        if ($Arg -match '^(?i)-Commit$' -and ($i + 1) -lt $Arguments.Count) {
            $CommitRef = [string]$Arguments[++$i]
            continue
        }
        if ($Arg -match '^(?i)-Tag$' -and ($i + 1) -lt $Arguments.Count) {
            $TagRef = [string]$Arguments[++$i]
            continue
        }
        if ($Arg -match '^(?i)-Branch$' -and ($i + 1) -lt $Arguments.Count) {
            $BranchRef = [string]$Arguments[++$i]
            continue
        }
        if ($Arg -match '^(?i)-Commit[:=](.+)$') {
            $CommitRef = $Matches[1]
            continue
        }
        if ($Arg -match '^(?i)-Tag[:=](.+)$') {
            $TagRef = $Matches[1]
            continue
        }
        if ($Arg -match '^(?i)-Branch[:=](.+)$') {
            $BranchRef = $Matches[1]
            continue
        }
    }

    if ($CommitRef) { return $CommitRef }
    if ($TagRef) { return $TagRef }
    if ($BranchRef) { return $BranchRef }
    return ''
}

$Repo = '9529360-cpu/stardust-hermes'
$ArgRef = Get-StardustInstallRef $args
$Ref = if ($env:STARDUST_INSTALL_REF) {
    $env:STARDUST_INSTALL_REF
} elseif ($ArgRef) {
    $ArgRef
} else {
    'main'
}
$InstallerUrl = "https://raw.githubusercontent.com/$Repo/$Ref/scripts/install.ps1"
$TempInstaller = Join-Path ([System.IO.Path]::GetTempPath()) ("stardust-install-{0}.ps1" -f ([guid]::NewGuid().ToString('N')))

try {
    $Source = (Invoke-WebRequest -UseBasicParsing -Uri $InstallerUrl).Content

    # Replace repository prefixes rather than only the .git URL. The Windows
    # installer also has archive/zip recovery paths beneath the same prefix.
    $Source = $Source.Replace('git@github.com:NousResearch/hermes-agent', 'git@github.com:9529360-cpu/stardust-hermes')
    $Source = $Source.Replace('https://github.com/NousResearch/hermes-agent', 'https://github.com/9529360-cpu/stardust-hermes')
    $Source = $Source.Replace('https://raw.githubusercontent.com/NousResearch/hermes-agent', 'https://raw.githubusercontent.com/9529360-cpu/stardust-hermes')
    $Source = $Source.Replace('https://hermes-agent.nousresearch.com/install.ps1', 'https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1')
    $Source = $Source.Replace('https://hermes-agent.nousresearch.com/install.sh', 'https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.sh')

    # The implementation still carries upstream copy because internal package,
    # command, and install-path names intentionally remain compatible. Rewrite
    # only the presentation strings that a Stardust user actually sees, plus
    # the auto-seeded SOUL identity: that one is runtime behavior, not branding.
    $Source = $Source.Replace('Hermes Agent Installer', 'Stardust Personal Assistant Installer')
    $Source = $Source.Replace('An open source AI agent by Nous Research.', 'Personal assistant built on the Hermes Agent foundation.')
    $Source = $Source.Replace('Download Hermes Agent', 'Download Stardust')
    $Source = $Source.Replace("You are Hermes Agent, built by Nous Research. Be direct:", "You are Stardust, a long-lived personal AI assistant and work orchestrator. Treat each user message first as intent: if the user is asking a question, discussing an idea, or wants advice, answer directly instead of turning it into an action workflow. When the user asks you to do work, use the available tools or delegate bounded work, keep the user's context stable, and ask only for missing decisions or approvals that materially belong to them. Work that must run later, recur, or survive a restart belongs on a durable scheduler or task rail, not process-local background delegation. Never let background work steal the user's focus; report useful state and terminal outcomes instead. Be direct:")

    $ForbiddenSourceUrls = @(
        'github.com/NousResearch/hermes-agent',
        'raw.githubusercontent.com/NousResearch/hermes-agent',
        'hermes-agent.nousresearch.com/install.ps1',
        'hermes-agent.nousresearch.com/install.sh'
    )
    foreach ($Forbidden in $ForbiddenSourceUrls) {
        if ($Source.Contains($Forbidden)) {
            throw "Stardust installer refused to run: upstream product-source URL survived rewriting: $Forbidden"
        }
    }

    [System.IO.File]::WriteAllText($TempInstaller, $Source, [System.Text.UTF8Encoding]::new($false))

    & $TempInstaller @args
    exit $LASTEXITCODE
}
finally {
    Remove-Item -LiteralPath $TempInstaller -Force -ErrorAction SilentlyContinue
}
