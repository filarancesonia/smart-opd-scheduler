"""Auth endpoints shared by every client (app, web, kiosk, IVR)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.core.deps import DbSession, get_current_user
from app.modules.identity import service
from app.modules.identity.schemas import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["Identity"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DbSession) -> UserOut:
    return UserOut.model_validate(service.register(db, payload))


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest, db: DbSession) -> TokenPair:
    _, tokens = service.login(db, payload)
    return tokens


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, db: DbSession) -> TokenPair:
    return service.refresh(db, payload.refresh_token)


@router.get("/me", response_model=UserOut)
def me(user=Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)
