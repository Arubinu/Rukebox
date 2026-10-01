@echo off
rem Windows blocks unsigned PowerShell scripts by default: this runs it once, without changing the policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rescue_ssh.ps1" %*
if errorlevel 1 pause
