@echo off
REM Runs provision_sdcard.ps1 despite the default execution policy. Double-click it.
powershell -NoProfile -Command "Unblock-File -Path '%~dp0provision_sdcard.ps1' -ErrorAction SilentlyContinue"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0provision_sdcard.ps1"
pause
