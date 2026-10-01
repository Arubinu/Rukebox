@echo off
powershell -NoProfile -Command "Unblock-File -Path '%~dp0usb_internet.ps1' -ErrorAction SilentlyContinue"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0usb_internet.ps1" %*
