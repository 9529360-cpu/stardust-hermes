@echo off
REM ============================================================================
REM Stardust Installer for Windows (CMD wrapper)
REM ============================================================================
REM This batch file launches the Stardust-owned PowerShell bootstrapper.
REM Product source and recovery stay pinned to 9529360-cpu/stardust-hermes.
REM
REM Usage:
REM   curl -fsSL https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install.cmd -o install.cmd && install.cmd && del install.cmd
REM
REM Or from PowerShell:
REM   iex (irm https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1)
REM ============================================================================

echo.
echo  Stardust Installer
echo  Launching Stardust PowerShell bootstrapper...
echo.

powershell -ExecutionPolicy ByPass -NoProfile -Command "iex (irm https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1)"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Installation failed. Please try running PowerShell directly:
    echo    powershell -ExecutionPolicy ByPass -c "iex (irm https://raw.githubusercontent.com/9529360-cpu/stardust-hermes/main/scripts/install-stardust.ps1)"
    echo.
    pause
    exit /b 1
)
