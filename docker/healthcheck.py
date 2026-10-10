#!/usr/bin/env python3
"""The container's health: the daemon answers on its socket and the web server answers HTTP."""

import sys
import urllib.error
import urllib.request

sys.path.insert(0, "/opt/rukebox/src")

from config_and_scan import load_config  # noqa: E402
from control_client import send_control_command  # noqa: E402


def main():
    cfg = load_config()
    status = send_control_command(cfg["CONTROL_SOCKET"], "get_status", timeout=5)
    if not status.get("ok", True) and str(status.get("error", "")).startswith("daemon_"):
        print("daemon: %s" % status.get("error"))
        return 1
    try:
        urllib.request.urlopen("http://127.0.0.1:%d/" % int(cfg.get("WEB_PORT") or 80), timeout=5).close()
    except urllib.error.HTTPError:
        pass  # An answer, even a refusal, is a web server alive.
    except (OSError, ValueError) as error:
        print("web: %s" % error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
