@echo off
rem Windows blocks unsigned PowerShell scripts by default: this runs the rescue
rem for one run only, without changing the execution policy permanently.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rescue_ssh.ps1" %*
if errorlevel 1 pause
