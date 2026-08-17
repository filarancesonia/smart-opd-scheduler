"""Audit middleware.

Every state-changing request is recorded: who, what, when, and the outcome.
Reads are not logged by default — logging every GET in a busy OPD would bury
the entries that matter and create its own surveillance problem.
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.database import SessionLocal
from app.core.security import TokenError, decode_token

logger = logging.getLogger("audit")

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

#: Paths whose bodies contain credentials — recorded, but never with detail.
SENSITIVE_PATHS = ("/auth/login", "/auth/register", "/auth/refresh")


def _actor(request: Request) -> tuple[int | None, str]:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None, "anonymous"
    try:
        payload = decode_token(header.split(" ", 1)[1])
    except TokenError:
        return None, "anonymous"
    return int(payload["sub"]), str(payload.get("role", "unknown"))


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        if request.method not in MUTATING_METHODS:
            return response

        try:
            self._record(request, response.status_code)
        except Exception:  # pragma: no cover - auditing must never 500 a request
            logger.exception("Failed to write audit entry for %s", request.url.path)
        return response

    @staticmethod
    def _session_factory(request: Request):
        """The app may supply its own factory; tests bind one to their engine."""
        return getattr(request.app.state, "audit_session_factory", None) or SessionLocal

    def _record(self, request: Request, status_code: int) -> None:
        from app.modules.privacy import service
        from app.modules.privacy.models import AuditAction

        path = request.url.path
        actor_id, role = _actor(request)

        if any(path.endswith(suffix) for suffix in SENSITIVE_PATHS):
            action = (
                AuditAction.LOGIN if status_code < 400 else AuditAction.LOGIN_FAILED
            )
            detail = ""  # never record anything from a credential payload
        else:
            action = {
                "POST": AuditAction.CREATE,
                "PUT": AuditAction.UPDATE,
                "PATCH": AuditAction.UPDATE,
                "DELETE": AuditAction.DELETE,
            }[request.method]
            detail = ""

        db = self._session_factory(request)()
        try:
            service.audit(
                db,
                action=action,
                resource_type=_resource_from(path),
                actor_user_id=actor_id,
                actor_role=role,
                method=request.method,
                path=path,
                status_code=status_code,
                client_fingerprint=service.fingerprint_client(
                    request.client.host if request.client else None,
                    request.headers.get("user-agent"),
                ),
                detail=detail,
            )
        finally:
            db.close()


def _resource_from(path: str) -> str:
    """First meaningful path segment after the API prefix."""
    parts = [segment for segment in path.split("/") if segment]
    for index, segment in enumerate(parts):
        if segment.startswith("v") and segment[1:].isdigit():
            return parts[index + 1] if index + 1 < len(parts) else "root"
    return parts[0] if parts else "root"
