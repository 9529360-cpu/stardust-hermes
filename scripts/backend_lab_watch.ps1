$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$Lab = Join-Path $Repo "scripts\backend_lab.py"
$Watch = "D:\DevTools\watchexec\bin\watchexec.exe"
Set-Location -LiteralPath $Repo

$args = @(
  "--restart", "--debounce", "2s", "--shell=none",
  "--exts", "py,md,toml,yaml,yml,json",
  "--watch", "agent", "--watch", "cron", "--watch", "gateway",
  "--watch", "hermes_cli", "--watch", "plugins", "--watch", "tools",
  "--watch", "tui_gateway", "--watch", "tests\agent",
  "--watch", "tests\gateway", "--watch", "tests\hermes_cli",
  "--watch", "tests\plugins", "--watch", "tests\tools",
  "--watch", "tests\tui_gateway", "--watch", "SOUL.md",
  "--watch", "run_agent.py", "--watch", "hermes_state_messages.py",
  "--watch", "hermes_state_rewind.py", "--watch", "scripts\backend_lab.py",
  "--", $Python, $Lab, "fast"
)
while ($true) {
  & $Watch @args
  Start-Sleep -Seconds 2
}
