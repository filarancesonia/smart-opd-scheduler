"""Registration, login and token refresh."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AuthenticationError, ConflictError
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.modules.identity.models import User
from app.modules.identity.schemas import (
    LoginRequest,
    RegisterRequest,
    TokenPair,
)


def get_by_phone(db: Session, phone: str) -> User | None:
    return db.execute(select(User).where(User.phone == phone)).scalar_one_or_none()


def register(db: Session, payload: RegisterRequest) -> User:
    if get_by_phone(db, payload.phone) is not None:
        raise ConflictError("An account with this mobile number already exists")
    if payload.email and db.execute(
        select(User).where(User.email == payload.email)
    ).scalar_one_or_none():
        raise ConflictError("An account with this email already exists")

    user = User(
        phone=payload.phone,
        full_name=payload.full_name.strip(),
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=str(payload.role),
        preferred_language=payload.preferred_language,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def issue_tokens(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(str(user.id), user.role),
        refresh_token=create_refresh_token(str(user.id), user.role),
        expires_in=settings.access_token_expire_minutes * 60,
    )


def login(db: Session, payload: LoginRequest) -> tuple[User, TokenPair]:
    user = get_by_phone(db, payload.phone)
    # Same message for unknown number and wrong password so the endpoint does
    # not confirm which mobile numbers are registered.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise AuthenticationError("Mobile number or password is incorrect")
    if not user.is_active:
        raise AuthenticationError("This account has been deactivated")
    return user, issue_tokens(user)


def refresh(db: Session, refresh_token: str) -> TokenPair:
    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except TokenError as exc:
        raise AuthenticationError(str(exc)) from exc

    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthenticationError("Account no longer active")
    return issue_tokens(user)
