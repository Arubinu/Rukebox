#!/usr/bin/env bash
# Runs the speaker test routine on the Pi. Optional argument: the Pi's address.
exec ssh -t "pi@${1:-rukebox.local}" "python3 /opt/rukebox/scripts/hardware_check.py"
