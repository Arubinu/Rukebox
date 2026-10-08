#!/usr/bin/env python3
"""What a container changes from the template on its very first start."""
# Written into the file, not the environment, which load_config() would let win for ever.

import sys

sys.path.insert(0, "/opt/rukebox/src")

import config_and_scan  # noqa: E402
import config_file  # noqa: E402

FIRST_START = {
    # The virtual sink: "Network stream" in the Audio output card.
    "AUDIO_OUTPUT": "docker",
    # On by default here, unlike on a Pi: with no card, it is the output.
    "STREAM_ENABLED": "true",
    # Nothing to wait for - there is no speaker to connect and no RTC to read;
    # the clock is the host's, already right.
    "CLOCK_SYNC_GRACE_SEC": "0",
    # No access point to sound a bell on, and no speaker to answer to one.
    "AP_CONNECT_SOUND": "",
    "BATTERY_LOW_SOUND": "",
}


def main():
    config_file._refresh_paths(force=True)
    updates = {key: value for key, value in FIRST_START.items()}
    try:
        config_and_scan.update_config_file(updates)
    except ValueError as error:
        print("first_start: %s" % error, file=sys.stderr)
        return 1
    print("first_start: %s" % ", ".join(
        "%s=%s" % (key, updates[key]) for key in sorted(updates)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
