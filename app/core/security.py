"""Password hashing, JWT issue/verify, and the role vocabulary.

Room 10 layers audit logging, consent and field encryption on top of these
primitives; everything here is the minimum every other module needs to
authenticate a caller.

Hashing uses stdlib scrypt (RFC 7914) rather than bcrypt so the project has no
native build dependency. Parameters follow the OWASP-recommended baseline.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

import jwt

from app.core.config import settings

# --- roles -----------------------------------------------------------------


class Role(StrEnum):
    PATIENT = "patient"
    DOCTOR = "doctor"
    STAFF = "staff"  # reception / kiosk operator
    ADMIN = "admin"  # hospital administrator
    HEALTH_DEPT = "health_dept"  # state Health Department viewer
    DEVICE = "device"  # Room 1 door hardware


#: Roles that may read aggregate analytics (Room 8).
ANALYTICS_ROLES = frozenset({Role.ADMIN, Role.HEALTH_DEPT})


# --- password hashing ------------------------------------------------------

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32


def hash_password(password: str) -> str:
    """Return a self-describing ``scrypt$n$r$p$salt$key`` hash string."""
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_BYTES,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of a password against a stored hash."""
    try:
        scheme, n, r, p, salt_hex, key_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(key_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, actual)


# --- device credential fingerprints ----------------------------------------


def credential_fingerprint(raw_value: str) -> str:
    """Keyed hash of an RFID tag / BLE id / face-template digest.

    Rooms 1 and 2 only ever persist this, never the raw identifier. It is a
    keyed HMAC rather than a plain hash so an attacker who steals the database
    still cannot brute-force the (short, low-entropy) tag number space without
    also holding the server secret.
    """
    return hmac.new(
        settings.secret_key.encode("utf-8"),
        raw_value.strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# --- JSON web tokens -------------------------------------------------------


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired or of the wrong type."""


def _create_token(
    subject: str, role: str, token_type: str, expires: timedelta, **extra: Any
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(subject),
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + expires,
        "jti": secrets.token_urlsafe(16),
        **extra,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str, role: str, **extra: Any) -> str:
    return _create_token(
        subject,
        role,
        "access",
        timedelta(minutes=settings.access_token_expire_minutes),
        **extra,
    )


def create_refresh_token(subject: str, role: str) -> str:
    return _create_token(
        subject, role, "refresh", timedelta(days=settings.refresh_token_expire_days)
    )


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    """Decode and validate a token, raising :class:`TokenError` on any problem."""
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Token is invalid") from exc

    if payload.get("type") != expected_type:
        raise TokenError(f"Expected a {expected_type} token")
    return payload
