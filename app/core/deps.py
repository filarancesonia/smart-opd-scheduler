"""Shared FastAPI dependencies: DB session, current user, role gates."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.errors import AuthenticationError, PermissionError_
from app.core.security import Role, TokenError, decode_token

DbSession = Annotated[Session, Depends(get_db)]

# auto_error=False so we can raise our own JSON-shaped 401 instead of FastAPI's.
_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
):
    """Resolve the bearer token to a live, active User row."""
    from app.modules.identity.models import User

    if credentials is None:
        raise AuthenticationError("Authorization header is missing")

    try:
        payload = decode_token(credentials.credentials)
    except TokenError as exc:
        raise AuthenticationError(str(exc)) from exc

    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthenticationError("Account no longer active")
    return user


CurrentUser = Annotated["object", Depends(get_current_user)]


def require_roles(*allowed: Role | str) -> Callable:
    """Dependency factory gating an endpoint to the given roles."""
    allowed_values = {str(r) for r in allowed}

    def _guard(user: CurrentUser):
        if str(user.role) not in allowed_values:
            raise PermissionError_(
                "Your role is not permitted to perform this action",
                details={"required": sorted(allowed_values)},
            )
        return user

    return _guard


def require_any_role(allowed: Iterable[Role | str]) -> Callable:
    return require_roles(*allowed)


def verify_device_key(x_device_key: Annotated[str | None, Header()] = None) -> str:
    """Room 1 door hardware authenticates with a shared key, not a JWT.

    Readers in corridors cannot hold user credentials, so they present a
    provisioned device key instead.
    """
    if not x_device_key or x_device_key != settings.device_api_key:
        raise AuthenticationError("Invalid or missing device key")
    return x_device_key


AdminUser = Annotated["object", Depends(require_roles(Role.ADMIN))]
