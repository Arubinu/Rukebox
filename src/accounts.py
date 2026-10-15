"""The accounts of this radio: the `people` block of rukebox.yaml.

An account is a name, a role (`roles.py`) and a password hash. No account at
all, or an owner account with no password, is the project's original model:
the interface is open to whoever reaches the access point. The first owner
account with a password closes it, and only then does anything here matter.
"""

import logging
import re
import time

import config_document
import roles
import web_auth

log = logging.getLogger("accounts")

BLOCK = "people"
NAME_MAX = 40
HASH_MAX = 200
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def _slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return slug[:32] or "person"


def _clean_id(raw):
    text = str(raw or "").strip().lower()
    return text if _ID_RE.match(text) else None


def _read_doc(path=None, strict=False):
    """The whole block: {"items": [{id, name, role, hash, created_at}]}."""
    data = config_document.read(BLOCK, path)
    if data is None:
        if strict:
            raise ValueError("file_unreadable")
        return {"items": []}
    items = data.get("items")
    data["items"] = [item for item in items if isinstance(item, dict) and item.get("id")] \
        if isinstance(items, list) else []
    return data


def load(path=None):
    """Every account, in the order the file holds them."""
    return _read_doc(path)["items"]


def read_items(path=None):
    """The accounts, or None when the document cannot be read at all."""
    try:
        return _read_doc(path, strict=True)["items"]
    except ValueError:
        return None


def _mutate(path, change):
    with config_document.lock(path):
        doc = _read_doc(path, strict=True)
        out = change(doc)
        if out is not None:
            config_document.write(BLOCK, out, path)
        return out


def save_all(items, path=None):
    """Replaces the whole list (a configuration import)."""
    with config_document.lock(path):
        doc = config_document.read(BLOCK, path)
        if not isinstance(doc, dict):
            doc = {}
        doc["items"] = items
        config_document.write(BLOCK, doc, path)


def validate(data, people=None):
    """A clean account, or ValueError carrying an error code."""
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("account_name_required")
    if len(name) > NAME_MAX:
        raise ValueError("account_name_too_long")
    role = str(data.get("role", "member") or "member").strip().lower()
    if role not in roles.ROLES[1:]:
        raise ValueError("account_bad_role")
    account_id = _clean_id(data.get("id")) or _slugify(name)
    clean = {"id": account_id, "name": name, "role": role}
    created = data.get("created_at")
    try:
        clean["created_at"] = float(created) if created else time.time()
    except (TypeError, ValueError):
        clean["created_at"] = time.time()
    stored = str(data.get("hash") or "")
    if stored and len(stored) > HASH_MAX:
        raise ValueError("account_bad_hash")
    if stored and not web_auth.is_hash(stored):
        raise ValueError("account_bad_hash")
    # Only the owner may hold the open door: an account without a password
    # would let anyone in by typing its name.
    if not stored and role != "owner":
        raise ValueError("account_password_required")
    clean["hash"] = stored
    for other in people if people is not None else load():
        if other["id"] != account_id and other["name"].casefold() == name.casefold():
            raise ValueError("account_name_taken")
    return clean


def by_id(account_id, path=None):
    for item in load(path):
        if item["id"] == account_id:
            return item
    return None


def by_name(name, path=None):
    wanted = str(name or "").strip().casefold()
    for item in load(path):
        if item["name"].casefold() == wanted or item["id"] == str(name or "").strip():
            return item
    return None


def owner(path=None):
    """The first owner account, or None."""
    for item in load(path):
        if item["role"] == "owner":
            return item
    return None


def owner_has_password(path=None):
    """Whether the door is closed: an owner account, with a password."""
    found = owner(path)
    return bool(found and found.get("hash"))


def add(data, path=None):
    """Creates an account, with its password when one is given."""
    password = str(data.get("password") or "")
    people = load(path)
    account_id = _clean_id(data.get("id")) or _slugify(data.get("name", ""))
    taken = {item["id"] for item in people}
    if account_id in taken:
        base, suffix = account_id, 2
        while account_id in taken:
            account_id = "%s-%d" % (base, suffix)
            suffix += 1
    candidate = dict(data, id=account_id)
    if password:
        if len(password) < web_auth.MIN_PASSWORD_LENGTH:
            raise ValueError("password_too_short")
        candidate["hash"] = web_auth.hash_password(password)
    clean = validate(candidate, people)
    _mutate(path, lambda doc: dict(doc, items=doc["items"] + [clean]))
    return clean


def update(account_id, data, path=None):
    """Partial update: fields left out keep their value, an empty password
    leaves the stored one alone."""
    found = []

    def change(doc):
        for index, item in enumerate(doc["items"]):
            if item["id"] != account_id:
                continue
            merged = dict(item)
            merged.update({key: value for key, value in data.items() if key != "password"})
            password = str(data.get("password") or "")
            if password:
                if len(password) < web_auth.MIN_PASSWORD_LENGTH:
                    raise ValueError("password_too_short")
                merged["hash"] = web_auth.hash_password(password)
            clean = validate(merged, doc["items"])
            doc["items"][index] = clean
            found.append(clean)
            return doc
        return None

    _mutate(path, change)
    if not found:
        raise KeyError(account_id)
    return found[0]


def set_password(account_id, password, path=None):
    """Sets or clears (password="") one account's password."""
    if password and len(password) < web_auth.MIN_PASSWORD_LENGTH:
        raise ValueError("password_too_short")
    hashed = web_auth.hash_password(password) if password else ""
    return update(account_id, {"hash": hashed}, path)


def delete(account_id, path=None):
    found = []

    def change(doc):
        remaining = [item for item in doc["items"] if item["id"] != account_id]
        if len(remaining) == len(doc["items"]):
            return None
        doc["items"] = remaining
        found.append(True)
        return doc

    _mutate(path, change)
    if not found:
        raise KeyError(account_id)


def verify(account, password):
    """Whether this password opens that account."""
    return bool(account) and web_auth.verify_password(password, account.get("hash") or "")


def migrate_legacy(legacy_hash, path=None, name="Owner"):
    """Gives the password this radio already had to its first owner account.

    Called at every boot until it has run: an installation that updates keeps
    logging in with the password it had, and the key it was kept in is left
    alone (the account becomes the one that is read).
    """
    if not legacy_hash or owner(path):
        return False
    add({"name": name, "role": "owner", "hash": legacy_hash}, path)
    log.info("The web password became the owner account %r", name)
    return True
