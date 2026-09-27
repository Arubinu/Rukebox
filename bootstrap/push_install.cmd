@echo off
REM Runs push_install.ps1 despite the default execution policy. Double-click it.
powershell -NoProfile -Command "Unblock-File -Path '%~dp0push_install.ps1' -ErrorAction SilentlyContinue"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0push_install.ps1" %*
pause
