#!/usr/bin/env python3
"""What a container changes from the template on its very first start."""
# Written into the file, not the environment, which load_config() would let win for ever.

import os
import sys

sys.path.insert(0, "/opt/rukebox/src")

import config_and_scan  # noqa: E402
import config_bundle  # noqa: E402
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
    # The server setup page's answers come last: they may choose another output.
    try:
        applied, password = config_bundle.apply_setup(os.environ.get(config_bundle.SETUP_ENV, ""),
                                                      os.environ.get(config_bundle.SETUP_HASH_ENV, ""))
    except ValueError as error:
        print("first_start: the prepared settings were refused (%s)" % error, file=sys.stderr)
        return 1
    if applied or password:
        print("first_start: %d prepared settings%s" % (applied, ", and the interface password" if password else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
