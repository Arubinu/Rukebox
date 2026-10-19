"""Secrets the radio must read back by itself, sealed so that a copy of the file is useless elsewhere.

The key is a random file of the state folder mixed with this machine's id: a
backup, an export or the card read on another computer cannot open what is
sealed. Root on the running radio can, which no key kept on it could prevent.
HMAC-SHA256 is the only primitive (the standard library has no cipher): a
counter-mode stream for the text, then a tag over everything (encrypt-then-MAC).
"""

import base64
import hashlib
import hmac
import json
import os
import secrets as random_bytes

import paths

VERSION = b"1"
KEY_FILE = "secret.key"
NONCE_SIZE = 16
TAG_SIZE = 32
MACHINE_ID_FILES = ("/etc/machine-id", "/var/lib/dbus/machine-id")


class SecretLost(Exception):
    """A sealed value this machine cannot open: another machine sealed it, or it was changed."""


def _machine_id():
    for path in MACHINE_ID_FILES:
        try:
            with open(path, encoding="ascii") as f:
                value = f.read().strip()
            if value:
                return value.encode("ascii")
        except (OSError, UnicodeDecodeError):
            continue
    return b""


def _write_private(path, data):
    """Writes `data` readable by this account alone, replacing the file in one step."""
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _keys(state_dir=None, create=False):
    """(stream key, tag key), or None when there is no key yet and none may be made."""
    path = os.path.join(state_dir or paths.state_dir(), KEY_FILE)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        raw = b""
    if len(raw) != 32:
        if not create:
            return None
        raw = random_bytes.token_bytes(32)
        _write_private(path, raw)
    root = hmac.new(raw, b"rukebox secrets\0" + _machine_id(), hashlib.sha256).digest()
    return (hmac.new(root, b"stream", hashlib.sha256).digest(),
            hmac.new(root, b"tag", hashlib.sha256).digest())


def _stream(key, nonce, size):
    out = bytearray()
    counter = 0
    while len(out) < size:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:size])


def seal(text, state_dir=None):
    """`text` sealed with this machine's key, as printable ASCII."""
    stream_key, tag_key = _keys(state_dir, create=True)
    nonce = random_bytes.token_bytes(NONCE_SIZE)
    data = str(text).encode("utf-8")
    body = bytes(a ^ b for a, b in zip(data, _stream(stream_key, nonce, len(data))))
    tag = hmac.new(tag_key, VERSION + nonce + body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(VERSION + nonce + body + tag).decode("ascii")


def unseal(token, state_dir=None):
    """The text a token holds; SecretLost when this machine did not seal it or it changed."""
    keys = _keys(state_dir)
    try:
        raw = base64.urlsafe_b64decode(str(token or "").encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        raise SecretLost("unreadable") from None
    if keys is None or len(raw) < 1 + NONCE_SIZE + TAG_SIZE or raw[:1] != VERSION:
        raise SecretLost("not sealed here")
    stream_key, tag_key = keys
    nonce, body, tag = raw[1:1 + NONCE_SIZE], raw[1 + NONCE_SIZE:-TAG_SIZE], raw[-TAG_SIZE:]
    if not hmac.compare_digest(tag, hmac.new(tag_key, VERSION + nonce + body, hashlib.sha256).digest()):
        raise SecretLost("not sealed here")
    data = bytes(a ^ b for a, b in zip(body, _stream(stream_key, nonce, len(body))))
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise SecretLost("unreadable") from None


class Store:
    """A JSON file of sealed secrets by name, readable by the service account alone."""

    def __init__(self, path, state_dir=None):
        self.path = path
        self.state_dir = state_dir

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def _save(self, data):
        _write_private(self.path, json.dumps(data, indent=2, sort_keys=True).encode("utf-8"))

    def names(self):
        return sorted(self._read())

    def has(self, name):
        return name in self._read()

    def get(self, name):
        """The secret, None when none is kept; SecretLost when it cannot be opened here."""
        token = self._read().get(name)
        return None if token is None else unseal(token, self.state_dir)

    def put(self, name, text):
        data = self._read()
        data[name] = seal(text, self.state_dir)
        self._save(data)

    def drop(self, name):
        data = self._read()
        if data.pop(name, None) is not None:
            self._save(data)

    def lost(self):
        """The names whose secret this machine cannot open (a backup restored elsewhere)."""
        out = []
        for name, token in sorted(self._read().items()):
            try:
                unseal(token, self.state_dir)
            except SecretLost:
                out.append(name)
        return out
