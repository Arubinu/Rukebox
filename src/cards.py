"""RFID cards: what each card starts when it is held on the reader."""

import re

import json_file

ACTIONS = ("list", "folder", "announcement", "action")
_ID_RE = re.compile(r"^[0-9A-Za-z]{4,32}$")


def load(path, strict=False):
    """{card id: {id, name, action, target}}; strict, an unreadable file raises
    instead of reading as empty - it must never be written over."""
    data = json_file.read(path)
    if data is None and strict:
        raise ValueError("file_unreadable")
    items = (data or {}).get("cards")
    found = {}
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and _ID_RE.match(str(item.get("id") or "")):
            found[item["id"]] = item
    return found


def validate(data):
    """A clean card, or ValueError carrying an error code."""
    card_id = str(data.get("id") or "").strip()
    if not _ID_RE.match(card_id):
        raise ValueError("card_bad_id")
    name = str(data.get("name") or "").strip()[:60]
    if not name:
        raise ValueError("card_name_required")
    action = str(data.get("action") or "")
    if action not in ACTIONS:
        raise ValueError("card_bad_action")
    target = str(data.get("target") or "").strip()[:500]
    if action != "list" and not target:
        raise ValueError("card_target_required")
    return {"id": card_id, "name": name, "action": action, "target": target}


def _write(path, cards):
    json_file.write(path, {"cards": sorted(cards.values(), key=lambda c: c["name"].lower())})


def save(path, data):
    """Adds or replaces one card."""
    card = validate(data)
    with json_file.lock(path):
        cards = load(path, strict=True)
        cards[card["id"]] = card
        _write(path, cards)
    return card


def delete(path, card_id):
    with json_file.lock(path):
        cards = load(path, strict=True)
        if card_id not in cards:
            raise KeyError(card_id)
        del cards[card_id]
        _write(path, cards)


def save_all(path, items):
    """Replaces the whole list (a configuration import)."""
    clean = {}
    for item in items:
        try:
            card = validate(item)
        except ValueError:
            continue
        clean[card["id"]] = card
    with json_file.lock(path):
        _write(path, clean)
