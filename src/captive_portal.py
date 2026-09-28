#!/usr/bin/env python3
"""Captive portal: makes a phone joining the access point open the web interface."""

import http.server
import ipaddress
import logging
import re
import socket
import subprocess
import threading
import time

log = logging.getLogger("web")

PROBE_DOMAINS = [
    "captive.apple.com",
    "connectivitycheck.gstatic.com",
    "connectivitycheck.android.com",
    "www.msftconnecttest.com",
    "detectportal.firefox.com",
    "connectivity-check.ubuntu.com",
]

PROBE_RESPONSES = {
    "/hotspot-detect.html": (
        200, "text/html",
        "<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>\n",
    ),
    "/library/test/success.html": (
        200, "text/html",
        "<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>\n",
    ),
    "/generate_204": (204, "text/plain", ""),
    "/gen_204": (204, "text/plain", ""),
    "/connecttest.txt": (200, "text/plain", "Microsoft Connect Test"),
    "/ncsi.txt": (200, "text/plain", "Microsoft NCSI"),
    "/canonical.html": (
        200, "text/html",
        '<meta http-equiv="refresh" content="0;url=https://support.mozilla.org/kb/captive-portal"/>\n',
    ),
    "/success.txt": (200, "text/plain", "success\n"),
}


def is_probe_path(path):
    """True if this request is an operating system asking "am I behind a
    captive portal?" rather than a person asking for a page."""
    return path.lower() in PROBE_RESPONSES


def probe_response(path):
    """(status, content_type, body) telling the OS the network is fine."""
    return PROBE_RESPONSES[path.lower()]


def redirect_url_for(remote_ip, ap_interface="uap0", web_port=80):
    """Where to send a device that just probed us, as an ABSOLUTE url."""
    target = ipaddress.ip_address(remote_ip) if remote_ip else None
    if target is not None:
        for address, network in _local_networks():
            if target in network:
                return "http://%s:%s/" % (address, web_port)
    ip = interface_ipv4(ap_interface)
    if ip:
        return "http://%s:%s/" % (ip, web_port)
    return None


def _local_networks(cache_seconds=30):
    """[(address, ip_network)] for every IPv4 address on this machine."""
    now = time.monotonic()
    cached = _networks_cache["value"]
    if cached and now - _networks_cache["at"] < cache_seconds:
        return cached
    found = []
    try:
        result = subprocess.run(
            ["ip", "-o", "-4", "addr", "show"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", line)
            if not match:
                continue
            interface = ipaddress.ip_interface("%s/%s" % (match.group(1), match.group(2)))
            found.append((match.group(1), interface.network))
    except (OSError, subprocess.SubprocessError, ValueError):
        found = []
    _networks_cache["at"] = now
    _networks_cache["value"] = found
    return found


_networks_cache = {"at": 0.0, "value": []}


PORTAL_PORT = 80


def interface_ipv4(iface):
    """The IPv4 address currently on `iface`, or None."""
    try:
        result = subprocess.run(
            ["ip", "-4", "-brief", "addr", "show", iface],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for field in result.stdout.split():
        if "/" in field:
            return field.split("/")[0]
    return None


def interface_network(iface, cache_seconds=30):
    """The IPv4 network on `iface`, or None."""
    now = time.monotonic()
    cached = _interface_networks.get(iface)
    if cached and now - cached[0] < cache_seconds:
        return cached[1]
    network = None
    try:
        result = subprocess.run(
            ["ip", "-o", "-4", "addr", "show", "dev", iface],
            capture_output=True, text=True, timeout=5,
        )
        match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", result.stdout)
        if match:
            parsed = ipaddress.ip_interface("%s/%s" % (match.group(1), match.group(2)))
            network = parsed.network
    except (OSError, subprocess.SubprocessError, ValueError):
        network = None
    _interface_networks[iface] = (now, network)
    return network


_interface_networks = {}


def is_ap_client(remote_ip, ap_interface="uap0"):
    """True for a device on the access point itself - the only place the
    portal holds anyone. A computer on the home network must never be told it
    has a Wi-Fi connection to finish."""
    try:
        target = ipaddress.ip_address(remote_ip)
    except ValueError:
        return False
    network = interface_network(ap_interface)
    return bool(network and target in network)


def _handler_class(target_url):
    class PortalHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", target_url)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.end_headers()

        do_POST = do_GET
        do_HEAD = do_GET

        def log_message(self, fmt, *args):
            pass

    return PortalHandler


class _Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def start(ap_interface, web_port):
    """Starts the redirect listener in a background thread."""
    ip = interface_ipv4(ap_interface)
    if not ip:
        log.warning(
            "Captive portal: %s has no IPv4 address yet, not starting "
            "(the access point may not be up).", ap_interface,
        )
        return None

    target_url = "http://%s:%s/" % (ip, web_port)
    try:
        server = _Server(("0.0.0.0", PORTAL_PORT), _handler_class(target_url))
    except OSError as e:
        log.warning(
            "Captive portal: could not listen on port %s (%s). The web "
            "interface itself is unaffected; port 80 needs "
            "CAP_NET_BIND_SERVICE, see systemd/rukebox-web.service.",
            PORTAL_PORT, e,
        )
        return None

    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("Captive portal listening on port %s, redirecting to %s", PORTAL_PORT, target_url)
    return server


def dnsmasq_config():
    """The dnsmasq drop-in that points the probe hostnames at this Pi."""
    lines = [
        "# Rukebox captive portal - generated, see src/captive_portal.py",
        "# Only these probe hostnames are redirected, so devices on the",
        "# hotspot keep normal internet access when the Pi has some.",
    ]
    lines += ["address=/%s/%s" % (domain, "%s") for domain in PROBE_DOMAINS]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "dnsmasq":
        sys.stdout.write(dnsmasq_config() % tuple([sys.argv[2]] * len(PROBE_DOMAINS)))
    else:
        print("Usage: captive_portal.py dnsmasq <address>")
