"""Room 10 — Security & Privacy (the lock).

Four tables, each answering a question the DPDP Act 2023 makes someone
accountable for:

  AuditLog            who touched what, and when
  Consent             what did this person actually agree to, and when
  FaceTemplate        the face data — a vector, never an image
  DataSubjectRequest  someone asked to see, correct, or delete their data
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin
from app.modules.privacy.crypto import EncryptedString


class ConsentPurpose(StrEnum):
    """Consent is per purpose. A blanket "I agree" is not consent."""

    FACE_RECOGNITION = "face_recognition"
    SMS_NOTIFICATIONS = "sms_notifications"
    WHATSAPP_NOTIFICATIONS = "whatsapp_notifications"
    ABHA_LINKING = "abha_linking"
    HMIS_SHARING = "hmis_sharing"
    RESEARCH_ANALYTICS = "research_analytics"


class ConsentStatus(StrEnum):
    GRANTED = "granted"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"


class AuditAction(StrEnum):
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    EXPORT = "export"
    CONSENT_GRANTED = "consent_granted"
    CONSENT_WITHDRAWN = "consent_withdrawn"
    ERASURE = "erasure"


class RequestType(StrEnum):
    """Data principal rights under the DPDP Act 2023."""

    ACCESS = "access"  # section 11 — right to know what is held
    CORRECTION = "correction"  # section 12 — right to correct
    ERASURE = "erasure"  # section 12 — right to erasure


class RequestStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    REJECTED = "rejected"


class AuditLog(TimestampMixin, Base):
    """Append-only. Nothing in the application ever updates or deletes a row."""

    __tablename__ = "audit_logs"

    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_role: Mapped[str] = mapped_column(String(20), default="anonymous", index=True)
    action: Mapped[str] = mapped_column(String(30), index=True)

    resource_type: Mapped[str] = mapped_column(String(50), index=True)
    resource_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    method: Mapped[str] = mapped_column(String(10), default="")
    path: Mapped[str] = mapped_column(String(255), default="")
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: Hashed, not stored raw. Enough to correlate a session, not enough to
    #: build a location history of a patient.
    client_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")


class Consent(TimestampMixin, Base):
    __tablename__ = "consents"

    #: Exactly one of these is set.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    patient_id: Mapped[int | None] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), nullable=True, index=True
    )

    purpose: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(
        String(20), default=ConsentStatus.GRANTED, index=True
    )

    #: Which version of the notice they were shown. Without this, "they
    #: consented" is not a defensible claim.
    notice_version: Mapped[str] = mapped_column(String(20), default="1.0")
    notice_language: Mapped[str] = mapped_column(String(5), default="hi")

    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    collected_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    note: Mapped[str] = mapped_column(Text, default="")


class FaceTemplate(TimestampMixin, Base):
    """A face embedding. Not a photograph — there is nowhere here to put one.

    The stored vector is a one-way projection produced by the recognition
    model at the door. It cannot be rendered back into a picture of a person,
    and this table has no column that could hold an image even if someone
    tried. Encrypted at rest on top of that, and tied to a specific consent
    record that can be withdrawn.
    """

    __tablename__ = "face_templates"

    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("doctors.id", ondelete="CASCADE"), index=True
    )
    consent_id: Mapped[int] = mapped_column(ForeignKey("consents.id"), index=True)

    #: JSON array of floats, encrypted.
    embedding: Mapped[str] = mapped_column(EncryptedString)
    dimensions: Mapped[int] = mapped_column(Integer)
    algorithm: Mapped[str] = mapped_column(String(40), default="facenet-512")
    #: Non-reversible digest, shared with Room 2 so a reader can be matched
    #: without this table being readable.
    template_digest: Mapped[str] = mapped_column(String(64), index=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    enrolled_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DataSubjectRequest(TimestampMixin, Base):
    __tablename__ = "data_subject_requests"

    request_type: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(
        String(20), default=RequestStatus.PENDING, index=True
    )

    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    patient_id: Mapped[int | None] = mapped_column(
        ForeignKey("patients.id", ondelete="SET NULL"), nullable=True, index=True
    )

    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    handled_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    detail: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str] = mapped_column(Text, default="")
