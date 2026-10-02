#!/usr/bin/env python3
"""Progress page of the automatic first-boot installation (standard library only)."""

import argparse
import http.server
import json
import mimetypes
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import captive_portal
except Exception:  # noqa: BLE001
    captive_portal = None

STATIC = {"/logo.webp", "/i18n.js", "/style.css", "/favicon.ico", "/favicon-32.png",
          "/apple-touch-icon.png", "/manifest.webmanifest"}
SECTION = re.compile(r"^== (.+) ==\s*$")
LOG_TAIL = 25


def read_status(status_file, log_file):
    try:
        with open(status_file, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {"step": 0, "total": 6, "key": "install.step_prepare", "detail": "", "error": None}
    lines = []
    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()[-400:]
    except OSError:
        pass
    section = next((m.group(1) for m in (SECTION.match(l) for l in reversed(lines)) if m), None)
    data["section"] = section
    data["log"] = lines[-LOG_TAIL:]
    return data


def make_handler(args):
    web_dir = os.path.abspath(args.web)

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "RukeboxInstall/1"

        def log_message(self, fmt, *a):
            pass

        def _send(self, code, body, ctype, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, payload):
            self._send(200, json.dumps(payload).encode("utf-8"), "application/json")

        def _file(self, path):
            try:
                with open(path, "rb") as f:
                    body = f.read()
            except OSError:
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            if path.endswith(".webmanifest"):
                ctype = "application/manifest+json"
            self._send(200, body, ctype)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/install/status":
                return self._json({"ok": True, "data": read_status(args.status, args.log)})
            if path == "/api/portal/status":
                return self._json({"ok": True, "data": {"installing": True}})
            if path.startswith("/api/"):
                return self._send(503, json.dumps({"ok": False, "error": "installing"}).encode(),
                                  "application/json")
            if path in ("/", "/index.html", "/installing.html"):
                return self._file(os.path.join(web_dir, "installing.html"))
            if path in STATIC:
                return self._file(os.path.join(web_dir, path.lstrip("/")))
            location = "/"
            if captive_portal is not None:
                try:
                    location = captive_portal.redirect_url_for(self.client_address[0]) or "/"
                except Exception:  # noqa: BLE001
                    location = "/"
            self._send(302, b"", "text/plain", {"Location": location})

    return Handler


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--port", type=int, default=80)
    p.add_argument("--status", default="/run/rukebox-install/status.json")
    p.add_argument("--log", default="/var/log/rukebox-install.log")
    p.add_argument("--web", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web"))
    args = p.parse_args(argv)
    server = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(args))
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
