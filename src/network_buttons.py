"""The buttons on other devices of the network: each shows up at its first press and waits to be approved."""

import hashlib
import hmac
import re
import time

import json_file

MAX_WAITING = 8
MAX_BUTTONS = 32
NAME_MAX = 40
_TOKEN = re.compile(r"[0-9a-f]{16,64}")


def _hash(secret):
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


def _read(path):
    items = (json_file.read(path) or {}).get("buttons")
    return [item for item in items if isinstance(item, dict) and item.get("id")] if isinstance(items, list) else []


def _clean_name(name, fallback=""):
    return re.sub(r"[\x00-\x1f\x7f]", "", str(name or "")).strip()[:NAME_MAX] or fallback


def load(path):
    """Every button, without its secret: those waiting first, then by name."""
    shown = [{key: item.get(key) for key in ("id", "name", "ip", "approved", "added_at")} for item in _read(path)]
    shown.sort(key=lambda item: (bool(item["approved"]), str(item["name"]).lower()))
    return shown


def press(path, button_id, secret, name, ip):
    """What a press from this button may do: approved, waiting, full or refused."""
    if not _TOKEN.fullmatch(str(button_id or "")) or not _TOKEN.fullmatch(str(secret or "")):
        return "refused"
    with json_file.lock(path):
        items = _read(path)
        known = next((item for item in items if item["id"] == button_id), None)
        if known is None:
            waiting = sum(1 for item in items if not item.get("approved"))
            if waiting >= MAX_WAITING or len(items) >= MAX_BUTTONS:
                return "full"
            items.append({"id": button_id, "secret_hash": _hash(secret), "ip": ip, "approved": False,
                          "name": _clean_name(name, ip or button_id[:8]), "added_at": int(time.time())})
            json_file.write(path, {"buttons": items})
            return "waiting"
        # The first press chose the secret: nobody else answers under that id.
        if not hmac.compare_digest(str(known.get("secret_hash") or ""), _hash(secret)):
            return "refused"
        if known.get("ip") != ip:
            known["ip"] = ip
            json_file.write(path, {"buttons": items})
        return "approved" if known.get("approved") else "waiting"


def update(path, button_id, approved=None, name=None):
    """Approves or names a button; False when it is not known."""
    with json_file.lock(path):
        items = _read(path)
        known = next((item for item in items if item["id"] == button_id), None)
        if known is None:
            return False
        if approved is not None:
            known["approved"] = bool(approved)
        if name is not None:
            known["name"] = _clean_name(name, known.get("name") or "")
        json_file.write(path, {"buttons": items})
    return True


def delete(path, button_id):
    """Forgets a button, which shows up again as waiting if it is pressed; False when it is not known."""
    with json_file.lock(path):
        items = _read(path)
        kept = [item for item in items if item["id"] != button_id]
        if len(kept) == len(items):
            return False
        json_file.write(path, {"buttons": kept})
    return True
