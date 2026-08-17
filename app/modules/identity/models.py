"""User accounts — the single identity every other module authenticates against."""

from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.core.security import Role


class User(TimestampMixin, Base):
    __tablename__ = "users"

    # Phone is the primary handle: every patient has one, many have no email.
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(150))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=Role.PATIENT, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Room 9 links this account to the national health ID once verified.
    abha_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)

    # Room 6 sends messages in the language the person actually reads.
    preferred_language: Mapped[str] = mapped_column(String(5), default="hi")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.id} {self.phone} {self.role}>"
