@echo off
rem Runs the speaker test routine on the Pi. Optional argument: the Pi's address.
chcp 65001 >nul
set "HOST=%~1"
if "%HOST%"=="" set "HOST=rukebox.local"
ssh -t pi@%HOST% "python3 /opt/rukebox/scripts/hardware_check.py"
pause
