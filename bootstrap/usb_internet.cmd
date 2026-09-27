@echo off
REM Runs usb_internet.ps1 despite the default execution policy. Double-click it.
powershell -NoProfile -Command "Unblock-File -Path '%~dp0usb_internet.ps1' -ErrorAction SilentlyContinue"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0usb_internet.ps1" %*
