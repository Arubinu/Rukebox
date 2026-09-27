"""Client for the daemon's Unix control socket."""

import json
import socket


def send_control_command(sock_path: str, cmd: str, timeout: float = 10.0, **kwargs):
    """Sends a command and returns the daemon's JSON response."""
    payload = {"cmd": cmd}
    payload.update(kwargs)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(sock_path)
            s.sendall(json.dumps(payload).encode("utf-8"))
            data = s.recv(65536)
    except OSError as e:
        return {"ok": False, "error": f"could not connect to daemon: {e}"}

    if not data:
        return {"ok": False, "error": "empty response from daemon"}
    try:
        return json.loads(data.decode("utf-8"))
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid response from daemon"}
