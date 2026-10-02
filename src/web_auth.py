"""Optional password for the web interface (PBKDF2, standard library)."""

import hashlib
import hmac
import os

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 260000
MIN_PASSWORD_LENGTH = 8
SALT_BYTES = 16


def hash_password(password):
    """Never store the return value of this anywhere but WEB_PASSWORD_HASH."""
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return "%s$%d$%s$%s" % (ALGORITHM, ITERATIONS, salt.hex(), digest.hex())


def verify_password(password, stored_hash):
    """Constant-time comparison - a plain `==` on the final hash would leak how
    many leading bytes matched via timing."""
    if not stored_hash or not password:
        return False
    try:
        algorithm, iterations_str, salt_hex, digest_hex = stored_hash.split("$")
        if algorithm != ALGORITHM:
            return False
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)
