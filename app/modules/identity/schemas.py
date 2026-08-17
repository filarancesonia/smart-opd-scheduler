"""Request/response shapes for authentication."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import Role

# Indian mobile numbers, with or without +91.
_PHONE_RE = re.compile(r"^(?:\+91)?[6-9]\d{9}$")


def normalise_phone(value: str) -> str:
    cleaned = re.sub(r"[\s\-()]", "", value or "")
    if not _PHONE_RE.match(cleaned):
        raise ValueError("Enter a valid 10-digit Indian mobile number")
    return cleaned[-10:]


class RegisterRequest(BaseModel):
    phone: str
    full_name: str = Field(min_length=2, max_length=150)
    password: str = Field(min_length=8, max_length=128)
    email: EmailStr | None = None
    role: Role = Role.PATIENT
    preferred_language: str = Field(default="hi", max_length=5)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalise_phone(v)


class LoginRequest(BaseModel):
    phone: str
    password: str

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalise_phone(v)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    phone: str
    full_name: str
    email: str | None
    role: str
    is_active: bool
    abha_id: str | None
    preferred_language: str
