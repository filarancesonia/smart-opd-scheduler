"""Field-level encryption for personal data at rest.

Health data is special category data under the DPDP Act 2023. Database-level
encryption protects against a stolen disk; it does nothing against a leaked
backup, an over-broad SELECT, or a support engineer with read access. So the
sensitive columns are encrypted in the application, and the database only ever
holds ciphertext.

The trade-off is real and worth stating plainly: an encrypted column cannot be
searched with `WHERE column = ?`. Where a field must stay searchable, store a
blind index alongside it — a keyed HMAC that supports exact-match lookup
without revealing the value.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from sqlalchemy import Text, TypeDecorator

from app.core.config import settings

#: Fixed salt: the key material is the secret, and a per-value salt would make
#: the derived key unreproducible across restarts.
_KDF_SALT = b"opd-field-encryption-v1"
_KDF_ITERATIONS = 200_000


@lru_cache(maxsize=4)
def _fernet_for(secret: str) -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_KDF_SALT,
        iterations=_KDF_ITERATIONS,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret.encode("utf-8")))
    return Fernet(key)


def _fernet() -> Fernet:
    return _fernet_for(settings.field_encryption_key)


def encrypt(plaintext: str) -> str:
    """AES-CBC with an HMAC tag, via Fernet. Output is URL-safe base64."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    """Decrypt, raising on any tampering."""
    return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")


def try_decrypt(ciphertext: str) -> str | None:
    try:
        return decrypt(ciphertext)
    except (InvalidToken, ValueError):
        return None


def blind_index(value: str) -> str:
    """Keyed HMAC for exact-match lookup on an encrypted column.

    Deterministic — that is the point — so it leaks equality but nothing else.
    Never use it on a low-entropy field where the candidate set is small
    enough to enumerate.
    """
    return hmac.new(
        settings.field_encryption_key.encode("utf-8"),
        value.strip().lower().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class EncryptedString(TypeDecorator):
    """A column that is plaintext in Python and ciphertext in the database."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt(str(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        # Tolerate rows written before encryption was switched on rather than
        # failing every read during a migration.
        return try_decrypt(value) or value
