@echo off
powershell -NoProfile -Command "Unblock-File -Path '%~dp0push_update.ps1' -ErrorAction SilentlyContinue"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0push_update.ps1" %*
pause
