"""Room 10 logic: audit, consent, face templates, and DPDP data rights."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import utcnow
from app.core.errors import ConflictError, NotFoundError, PermissionError_, ValidationError
from app.modules.booking.models import Appointment, Patient
from app.modules.identity.models import User
from app.modules.privacy.crypto import blind_index
from app.modules.privacy.models import (
    AuditAction,
    AuditLog,
    Consent,
    ConsentPurpose,
    ConsentStatus,
    DataSubjectRequest,
    FaceTemplate,
    RequestStatus,
    RequestType,
)
from app.modules.privacy.schemas import (
    ConsentNotice,
    ErasureResult,
    FaceMatchResult,
    PersonalDataExport,
    PrivacyStatus,
)

#: Below this cosine similarity a face is not considered the same person.
DEFAULT_MATCH_THRESHOLD = 0.75

#: Values that mean the operator never changed the shipped defaults.
_INSECURE_DEFAULTS = {"change-me-in-production", "change-me-too", ""}


# --- audit -----------------------------------------------------------------


def audit(
    db: Session,
    *,
    action: AuditAction,
    resource_type: str,
    resource_id: str | int | None = None,
    actor_user_id: int | None = None,
    actor_role: str = "anonymous",
    method: str = "",
    path: str = "",
    status_code: int | None = None,
    client_fingerprint: str = "",
    detail: str = "",
) -> AuditLog:
    entry = AuditLog(
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        action=str(action),
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        method=method,
        path=path,
        status_code=status_code,
        client_fingerprint=client_fingerprint,
        occurred_at=utcnow(),
        detail=detail,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def fingerprint_client(ip: str | None, user_agent: str | None) -> str:
    """Hash rather than store. Enough to correlate a session, not to track."""
    raw = f"{ip or ''}|{user_agent or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def list_audit_logs(
    db: Session,
    *,
    actor_user_id: int | None = None,
    action: AuditAction | None = None,
    resource_type: str | None = None,
    limit: int = 200,
) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.occurred_at.desc())
    if actor_user_id is not None:
        stmt = stmt.where(AuditLog.actor_user_id == actor_user_id)
    if action is not None:
        stmt = stmt.where(AuditLog.action == str(action))
    if resource_type is not None:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    return list(db.execute(stmt.limit(limit)).scalars())


# --- consent ---------------------------------------------------------------

NOTICES: dict[str, ConsentNotice] = {
    ConsentPurpose.FACE_RECOGNITION: ConsentNotice(
        purpose=ConsentPurpose.FACE_RECOGNITION,
        version="1.0",
        title_hi="चेहरा पहचान के लिए सहमति",
        title_en="Consent for face recognition",
        body_hi=(
            "आपकी उपस्थिति दर्ज करने के लिए दरवाज़े पर लगा कैमरा आपके चेहरे से "
            "एक गणितीय संख्या-श्रृंखला बनाएगा। आपकी तस्वीर कभी सहेजी नहीं जाएगी और "
            "इस संख्या से आपका चेहरा दोबारा नहीं बनाया जा सकता। "
            "यह केवल यह जाँचने के लिए है कि आप ड्यूटी पर पहुँचे या नहीं।"
        ),
        body_en=(
            "To record your attendance, the camera at the door will convert your "
            "face into a mathematical vector. Your photograph is never stored, and "
            "your face cannot be reconstructed from this vector. It is used only to "
            "confirm whether you have arrived for duty."
        ),
        retention_note_hi=(
            "आप कभी भी यह सहमति वापस ले सकते हैं; वापस लेते ही यह संख्या मिटा दी जाएगी "
            "और उपस्थिति RFID कार्ड से दर्ज होगी।"
        ),
        retention_note_en=(
            "You may withdraw this consent at any time. The vector is destroyed "
            "immediately on withdrawal and attendance reverts to the RFID card."
        ),
    ),
    ConsentPurpose.ABHA_LINKING: ConsentNotice(
        purpose=ConsentPurpose.ABHA_LINKING,
        version="1.0",
        title_hi="ABHA स्वास्थ्य आईडी जोड़ने की सहमति",
        title_en="Consent to link your ABHA health ID",
        body_hi=(
            "आपकी ABHA आईडी जोड़ने से आपका अस्पताल रिकॉर्ड आपके राष्ट्रीय स्वास्थ्य "
            "रिकॉर्ड से जुड़ जाएगा। केवल इस मुलाक़ात से जुड़ी जानकारी साझा की जाएगी।"
        ),
        body_en=(
            "Linking your ABHA ID connects this hospital record to your national "
            "health record. Only information about this visit is shared."
        ),
        retention_note_hi="आप यह लिंक कभी भी हटा सकते हैं।",
        retention_note_en="You may remove this link at any time.",
    ),
    ConsentPurpose.SMS_NOTIFICATIONS: ConsentNotice(
        purpose=ConsentPurpose.SMS_NOTIFICATIONS,
        version="1.0",
        title_hi="SMS सूचना के लिए सहमति",
        title_en="Consent for SMS notifications",
        body_hi=(
            "हम आपको अपॉइंटमेंट की पुष्टि, याद दिलाने और डॉक्टर की उपलब्धता के बारे में "
            "SMS भेजेंगे। आपका नंबर किसी और को नहीं दिया जाएगा।"
        ),
        body_en=(
            "We will send you SMS messages confirming your appointment, reminding "
            "you before it, and telling you if the doctor is delayed. Your number "
            "is not shared with anyone else."
        ),
        retention_note_hi="आप कभी भी SMS बंद करवा सकते हैं।",
        retention_note_en="You may stop these messages at any time.",
    ),
}


def get_notice(purpose: ConsentPurpose) -> ConsentNotice:
    notice = NOTICES.get(str(purpose))
    if notice is None:
        raise NotFoundError("No consent notice is published for that purpose")
    return notice


def active_consent(
    db: Session,
    purpose: ConsentPurpose,
    *,
    user_id: int | None = None,
    patient_id: int | None = None,
) -> Consent | None:
    stmt = select(Consent).where(
        Consent.purpose == str(purpose), Consent.status == ConsentStatus.GRANTED
    )
    if user_id is not None:
        stmt = stmt.where(Consent.user_id == user_id)
    if patient_id is not None:
        stmt = stmt.where(Consent.patient_id == patient_id)
    return db.execute(stmt.order_by(Consent.granted_at.desc())).scalars().first()


def grant_consent(
    db: Session,
    purpose: ConsentPurpose,
    *,
    user_id: int | None = None,
    patient_id: int | None = None,
    notice_version: str = "1.0",
    notice_language: str = "hi",
    collected_by_user_id: int | None = None,
    note: str = "",
) -> Consent:
    if (user_id is None) == (patient_id is None):
        raise ValidationError("Provide exactly one of user_id or patient_id")

    existing = active_consent(db, purpose, user_id=user_id, patient_id=patient_id)
    if existing is not None:
        return existing

    consent = Consent(
        user_id=user_id,
        patient_id=patient_id,
        purpose=str(purpose),
        status=str(ConsentStatus.GRANTED),
        notice_version=notice_version,
        notice_language=notice_language,
        granted_at=utcnow(),
        collected_by_user_id=collected_by_user_id,
        note=note,
    )
    db.add(consent)
    db.commit()
    db.refresh(consent)

    audit(
        db,
        action=AuditAction.CONSENT_GRANTED,
        resource_type="consent",
        resource_id=consent.id,
        actor_user_id=collected_by_user_id,
        detail=f"{purpose} (notice {notice_version})",
    )
    return consent


def withdraw_consent(db: Session, consent_id: int, actor_user_id: int | None) -> Consent:
    """Withdrawal must actually destroy what the consent permitted."""
    consent = db.get(Consent, consent_id)
    if consent is None:
        raise NotFoundError("Consent record not found")
    if consent.status != ConsentStatus.GRANTED:
        raise ConflictError(f"This consent is already {consent.status}")

    consent.status = str(ConsentStatus.WITHDRAWN)
    consent.withdrawn_at = utcnow()
    db.commit()

    if consent.purpose == ConsentPurpose.FACE_RECOGNITION:
        _destroy_face_templates(db, consent.id)
    if consent.purpose == ConsentPurpose.ABHA_LINKING and consent.patient_id:
        _revoke_abha_links(db, consent.patient_id)

    audit(
        db,
        action=AuditAction.CONSENT_WITHDRAWN,
        resource_type="consent",
        resource_id=consent.id,
        actor_user_id=actor_user_id,
        detail=f"{consent.purpose} withdrawn; dependent data destroyed",
    )
    db.refresh(consent)
    return consent


def list_consents(
    db: Session, *, user_id: int | None = None, patient_id: int | None = None
) -> list[Consent]:
    stmt = select(Consent).order_by(Consent.granted_at.desc())
    if user_id is not None:
        stmt = stmt.where(Consent.user_id == user_id)
    if patient_id is not None:
        stmt = stmt.where(Consent.patient_id == patient_id)
    return list(db.execute(stmt).scalars())


# --- face templates --------------------------------------------------------


def _digest(embedding: list[float]) -> str:
    """Stable, non-reversible digest shared with Rooms 1 and 2."""
    quantised = ",".join(f"{value:.4f}" for value in embedding)
    return blind_index(quantised)


def _normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0:
        raise ValidationError("A face embedding cannot be all zeros")
    return [component / norm for component in vector]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(_normalise(a), _normalise(b)))


def enrol_face(
    db: Session,
    doctor_id: int,
    embedding: list[float],
    *,
    algorithm: str = "facenet-512",
    actor_user_id: int | None = None,
) -> FaceTemplate:
    """Store a face vector — only with a live consent on record."""
    from app.modules.doctors import service as doctors_service

    doctor = doctors_service.get_doctor(db, doctor_id)
    consent = active_consent(
        db, ConsentPurpose.FACE_RECOGNITION, user_id=doctor.user_id
    )
    if consent is None:
        # Enrolling a face without consent is exactly the thing the DPDP Act
        # is about. There is no override path here on purpose.
        raise PermissionError_(
            "This doctor has not consented to face recognition",
            details={
                "required_consent": str(ConsentPurpose.FACE_RECOGNITION),
                "hint": "Show the notice and record consent first",
            },
        )

    normalised = _normalise(embedding)
    digest = _digest(normalised)

    for previous in _templates_for(db, doctor_id):
        previous.is_active = False
        previous.retired_at = utcnow()

    template = FaceTemplate(
        doctor_id=doctor_id,
        consent_id=consent.id,
        embedding=json.dumps(normalised),
        dimensions=len(normalised),
        algorithm=algorithm,
        template_digest=digest,
        is_active=True,
        enrolled_by_user_id=actor_user_id,
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    _register_with_room_2(db, doctor_id, digest)

    audit(
        db,
        action=AuditAction.CREATE,
        resource_type="face_template",
        resource_id=template.id,
        actor_user_id=actor_user_id,
        detail=f"Enrolled {len(normalised)}-dimension vector for doctor {doctor_id}",
    )
    return template


def _register_with_room_2(db: Session, doctor_id: int, digest: str) -> None:
    """Give Room 2 the digest so a door reader can match without this table."""
    try:
        from app.modules.doctors import service as doctors_service
        from app.modules.doctors.models import CredentialType
        from app.modules.doctors.schemas import CredentialCreate

        doctors_service.add_credential(
            db,
            doctor_id,
            CredentialCreate(
                credential_type=CredentialType.FACE,
                raw_value=digest,
                label="Face template",
            ),
        )
    except ConflictError:
        pass  # already registered


def _templates_for(db: Session, doctor_id: int) -> list[FaceTemplate]:
    return list(
        db.execute(
            select(FaceTemplate).where(
                FaceTemplate.doctor_id == doctor_id, FaceTemplate.is_active.is_(True)
            )
        ).scalars()
    )


def _destroy_face_templates(db: Session, consent_id: int) -> int:
    """Erase, not deactivate. Withdrawal means the vector is gone."""
    templates = list(
        db.execute(
            select(FaceTemplate).where(FaceTemplate.consent_id == consent_id)
        ).scalars()
    )
    for template in templates:
        db.delete(template)
    db.commit()
    return len(templates)


def match_face(
    db: Session, embedding: list[float], threshold: float = DEFAULT_MATCH_THRESHOLD
) -> FaceMatchResult:
    """Compare a live capture against enrolled templates."""
    probe = _normalise(embedding)
    templates = list(
        db.execute(
            select(FaceTemplate).where(FaceTemplate.is_active.is_(True))
        ).scalars()
    )
    if not templates:
        return FaceMatchResult(matched=False, reason="No faces are enrolled")

    best = None
    best_score = -1.0
    for template in templates:
        stored = json.loads(template.embedding)
        if len(stored) != len(probe):
            continue
        score = cosine_similarity(probe, stored)
        if score > best_score:
            best, best_score = template, score

    if best is None:
        return FaceMatchResult(
            matched=False, reason="No enrolled template of matching dimensions"
        )
    if best_score < threshold:
        return FaceMatchResult(
            matched=False,
            similarity=round(best_score, 4),
            reason="Below the match threshold",
        )
    return FaceMatchResult(
        matched=True,
        doctor_id=best.doctor_id,
        similarity=round(best_score, 4),
        template_digest=best.template_digest,
    )


def list_templates(db: Session, doctor_id: int | None = None) -> list[FaceTemplate]:
    stmt = select(FaceTemplate).order_by(FaceTemplate.created_at.desc())
    if doctor_id is not None:
        stmt = stmt.where(FaceTemplate.doctor_id == doctor_id)
    return list(db.execute(stmt).scalars())


# --- data subject rights ---------------------------------------------------


def _revoke_abha_links(db: Session, patient_id: int) -> int:
    try:
        from app.modules.integration import service as integration_service
        from app.modules.integration.models import ExternalSystem

        links = integration_service.list_links(
            db, system=ExternalSystem.ABHA, patient_id=patient_id
        )
        for link in links:
            integration_service.revoke_link(db, link.id)
        return len(links)
    except ImportError:  # pragma: no cover - defensive
        return 0


def create_request(
    db: Session,
    request_type: RequestType,
    *,
    user_id: int | None = None,
    patient_id: int | None = None,
    detail: str = "",
) -> DataSubjectRequest:
    request = DataSubjectRequest(
        request_type=str(request_type),
        status=str(RequestStatus.PENDING),
        user_id=user_id,
        patient_id=patient_id,
        requested_at=utcnow(),
        detail=detail,
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def export_personal_data(db: Session, user: User) -> PersonalDataExport:
    """Everything held about one person — the DPDP right of access."""
    from app.modules.booking import service as booking_service

    patient = db.execute(
        select(Patient).where(Patient.user_id == user.id)
    ).scalar_one_or_none()

    appointments = []
    notifications = []
    external_links = []
    consents = [
        {
            "purpose": c.purpose,
            "status": c.status,
            "granted_at": c.granted_at.isoformat(),
            "withdrawn_at": c.withdrawn_at.isoformat() if c.withdrawn_at else None,
            "notice_version": c.notice_version,
        }
        for c in list_consents(db, user_id=user.id)
    ]

    if patient is not None:
        consents += [
            {
                "purpose": c.purpose,
                "status": c.status,
                "granted_at": c.granted_at.isoformat(),
                "withdrawn_at": c.withdrawn_at.isoformat() if c.withdrawn_at else None,
                "notice_version": c.notice_version,
            }
            for c in list_consents(db, patient_id=patient.id)
        ]
        appointments = [
            {
                "booking_reference": a.booking_reference,
                "date": a.appointment_date.isoformat(),
                "time": a.slot_start.isoformat(),
                "status": a.status,
                "channel": a.channel,
                "room": a.room,
            }
            for a in booking_service.list_appointments(db, patient_id=patient.id)
        ]
        try:
            from app.modules.notifications import service as notifications_service

            notifications = [
                {
                    "channel": n.channel,
                    "template": n.template_code,
                    "status": n.status,
                    "sent_at": n.sent_at.isoformat() if n.sent_at else None,
                }
                for n in notifications_service.list_notifications(
                    db, patient_id=patient.id
                )
            ]
        except ImportError:  # pragma: no cover
            pass
        try:
            from app.modules.integration import service as integration_service

            external_links = [
                {"system": link.system, "status": link.status}
                for link in integration_service.list_links(db, patient_id=patient.id)
            ]
        except ImportError:  # pragma: no cover
            pass

    return PersonalDataExport(
        generated_at=utcnow(),
        account={
            "id": user.id,
            "full_name": user.full_name,
            "phone": user.phone,
            "email": user.email,
            "role": user.role,
            "preferred_language": user.preferred_language,
        },
        patient_record=(
            {
                "id": patient.id,
                "full_name": patient.full_name,
                "phone": patient.phone,
                "age": patient.age,
                "gender": patient.gender,
                "abha_id": patient.abha_id,
            }
            if patient
            else None
        ),
        appointments=appointments,
        consents=consents,
        notifications=notifications,
        external_links=external_links,
        note=(
            "Clinical records required to be retained under medical record "
            "rules are listed but not erasable on request."
        ),
    )


def perform_erasure(
    db: Session, request_id: int, actor_user_id: int | None
) -> ErasureResult:
    """Anonymise rather than delete outright.

    A completed consultation is a medical record with its own retention rules,
    and deleting the row would corrupt the hospital's clinical history. What
    can go is everything that identifies the person.
    """
    request = db.get(DataSubjectRequest, request_id)
    if request is None:
        raise NotFoundError("Request not found")
    if request.status != RequestStatus.PENDING:
        raise ConflictError(f"This request is already {request.status}")

    anonymised = revoked = 0
    retired = deleted_consents = 0
    retained: list[str] = []

    patient = None
    if request.patient_id:
        patient = db.get(Patient, request.patient_id)
    elif request.user_id:
        patient = db.execute(
            select(Patient).where(Patient.user_id == request.user_id)
        ).scalar_one_or_none()

    if patient is not None:
        appointments = db.execute(
            select(func.count(Appointment.id)).where(
                Appointment.patient_id == patient.id
            )
        ).scalar_one()
        if appointments:
            retained.append(
                f"{appointments} clinical appointment record(s) retained in "
                "anonymised form under medical record retention rules"
            )

        patient.full_name = f"Erased patient {patient.id}"
        patient.phone = "0000000000"
        patient.address = ""
        patient.abha_id = None
        patient.user_id = None
        anonymised = 1
        revoked = _revoke_abha_links(db, patient.id)

        for consent in list_consents(db, patient_id=patient.id):
            db.delete(consent)
            deleted_consents += 1

    if request.user_id:
        user = db.get(User, request.user_id)
        if user is not None:
            from app.modules.doctors.models import Doctor

            doctor = db.execute(
                select(Doctor).where(Doctor.user_id == user.id)
            ).scalar_one_or_none()
            if doctor is not None:
                for template in list_templates(db, doctor.id):
                    db.delete(template)
                    retired += 1

            user.is_active = False
            user.full_name = f"Erased user {user.id}"
            user.email = None
            for consent in list_consents(db, user_id=user.id):
                db.delete(consent)
                deleted_consents += 1

    request.status = str(RequestStatus.COMPLETED)
    request.completed_at = utcnow()
    request.handled_by_user_id = actor_user_id
    request.outcome = "Personal identifiers removed; clinical records anonymised"
    db.commit()

    audit(
        db,
        action=AuditAction.ERASURE,
        resource_type="data_subject_request",
        resource_id=request_id,
        actor_user_id=actor_user_id,
        detail=request.outcome,
    )

    return ErasureResult(
        request_id=request_id,
        anonymised_patient_records=anonymised,
        deleted_consents=deleted_consents,
        retired_face_templates=retired,
        revoked_external_links=revoked,
        retained=retained,
    )


def list_requests(
    db: Session, *, status: RequestStatus | None = None
) -> list[DataSubjectRequest]:
    stmt = select(DataSubjectRequest).order_by(DataSubjectRequest.requested_at.desc())
    if status is not None:
        stmt = stmt.where(DataSubjectRequest.status == str(status))
    return list(db.execute(stmt).scalars())


# --- compliance summary ----------------------------------------------------


def privacy_status(db: Session) -> PrivacyStatus:
    granted = db.execute(
        select(func.count(Consent.id)).where(Consent.status == ConsentStatus.GRANTED)
    ).scalar_one()
    withdrawn = db.execute(
        select(func.count(Consent.id)).where(Consent.status == ConsentStatus.WITHDRAWN)
    ).scalar_one()
    templates = db.execute(
        select(func.count(FaceTemplate.id)).where(FaceTemplate.is_active.is_(True))
    ).scalar_one()
    audits = db.execute(select(func.count(AuditLog.id))).scalar_one()
    pending = db.execute(
        select(func.count(DataSubjectRequest.id)).where(
            DataSubjectRequest.status == RequestStatus.PENDING
        )
    ).scalar_one()

    key_ok = settings.field_encryption_key not in _INSECURE_DEFAULTS
    notes = [
        "Face data is stored as a one-way vector; no image is retained anywhere.",
        "Personal identifiers are encrypted at rest with a key held outside the database.",
        "Consent is recorded per purpose, with the notice version shown to the person.",
        "Withdrawing face-recognition consent destroys the stored vector immediately.",
        "Erasure anonymises clinical records rather than deleting them, as medical "
        "record retention rules require.",
    ]
    if not key_ok:
        notes.append(
            "WARNING: FIELD_ENCRYPTION_KEY is still the shipped default. "
            "Generate a real key before handling live patient data."
        )
    if settings.secret_key in _INSECURE_DEFAULTS:
        notes.append(
            "WARNING: SECRET_KEY is still the shipped default. Tokens and "
            "credential fingerprints are not secure."
        )

    return PrivacyStatus(
        encryption_enabled=True,
        encryption_key_configured=key_ok,
        audit_log_entries=int(audits),
        consents_granted=int(granted),
        consents_withdrawn=int(withdrawn),
        face_templates_active=int(templates),
        #: Structurally zero — there is no column anywhere that holds an image.
        face_images_stored=0,
        pending_data_requests=int(pending),
        dpdp_notes=notes,
    )
