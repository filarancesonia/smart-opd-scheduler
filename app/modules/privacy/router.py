"""Room 10 endpoints — consent, face templates, audit, and DPDP data rights."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status as http_status

from app.core.deps import DbSession, get_current_user, require_roles
from app.core.security import Role
from app.modules.privacy import service
from app.modules.privacy.models import (
    AuditAction,
    ConsentPurpose,
    RequestStatus,
    RequestType,
)
from app.modules.privacy.schemas import (
    AuditLogOut,
    ConsentGrant,
    ConsentNotice,
    ConsentOut,
    DataSubjectRequestCreate,
    DataSubjectRequestOut,
    ErasureResult,
    FaceEnrolRequest,
    FaceMatchRequest,
    FaceMatchResult,
    FaceTemplateOut,
    PersonalDataExport,
    PrivacyStatus,
)

router = APIRouter(prefix="/privacy", tags=["Room 10 - Security & Privacy"])

AdminOnly = Depends(require_roles(Role.ADMIN))
StaffOrAdmin = Depends(require_roles(Role.ADMIN, Role.STAFF))


@router.get("/status", response_model=PrivacyStatus, dependencies=[AdminOnly])
def privacy_status(db: DbSession) -> PrivacyStatus:
    """Compliance summary, including warnings about unchanged default keys."""
    return service.privacy_status(db)


# --- consent ---------------------------------------------------------------


@router.get("/notices/{purpose}", response_model=ConsentNotice)
def get_notice(purpose: ConsentPurpose) -> ConsentNotice:
    """The plain-language notice that must be shown before consent is taken."""
    return service.get_notice(purpose)


@router.post(
    "/consents",
    response_model=ConsentOut,
    status_code=http_status.HTTP_201_CREATED,
    dependencies=[StaffOrAdmin],
)
def grant_consent(
    payload: ConsentGrant, db: DbSession, user=Depends(get_current_user)
) -> ConsentOut:
    consent = service.grant_consent(
        db,
        payload.purpose,
        user_id=payload.user_id,
        patient_id=payload.patient_id,
        notice_version=payload.notice_version,
        notice_language=payload.notice_language,
        collected_by_user_id=user.id,
        note=payload.note,
    )
    return ConsentOut.model_validate(consent)


@router.get("/consents", response_model=list[ConsentOut], dependencies=[StaffOrAdmin])
def list_consents(
    db: DbSession, user_id: int | None = None, patient_id: int | None = None
) -> list[ConsentOut]:
    return [
        ConsentOut.model_validate(c)
        for c in service.list_consents(db, user_id=user_id, patient_id=patient_id)
    ]


@router.get("/consents/me", response_model=list[ConsentOut])
def my_consents(db: DbSession, user=Depends(get_current_user)) -> list[ConsentOut]:
    return [
        ConsentOut.model_validate(c) for c in service.list_consents(db, user_id=user.id)
    ]


@router.delete("/consents/{consent_id}", response_model=ConsentOut)
def withdraw_consent(
    consent_id: int, db: DbSession, user=Depends(get_current_user)
) -> ConsentOut:
    """Withdrawal destroys whatever the consent permitted, not just the flag."""
    consent = service.withdraw_consent(db, consent_id, user.id)
    return ConsentOut.model_validate(consent)


# --- face templates --------------------------------------------------------


@router.post(
    "/face/enrol",
    response_model=FaceTemplateOut,
    status_code=http_status.HTTP_201_CREATED,
    dependencies=[AdminOnly],
)
def enrol_face(
    payload: FaceEnrolRequest, db: DbSession, user=Depends(get_current_user)
) -> FaceTemplateOut:
    """Store a face vector. Refused outright without a live consent record."""
    template = service.enrol_face(
        db,
        payload.doctor_id,
        payload.embedding,
        algorithm=payload.algorithm,
        actor_user_id=user.id,
    )
    return FaceTemplateOut.model_validate(template)


@router.post("/face/match", response_model=FaceMatchResult, dependencies=[AdminOnly])
def match_face(payload: FaceMatchRequest, db: DbSession) -> FaceMatchResult:
    """Compare a live capture against enrolled vectors."""
    return service.match_face(db, payload.embedding, payload.threshold)


@router.get(
    "/face/templates", response_model=list[FaceTemplateOut], dependencies=[AdminOnly]
)
def list_templates(db: DbSession, doctor_id: int | None = None) -> list[FaceTemplateOut]:
    """Metadata only — the vector itself never leaves the server."""
    return [
        FaceTemplateOut.model_validate(t) for t in service.list_templates(db, doctor_id)
    ]


# --- audit -----------------------------------------------------------------


@router.get("/audit", response_model=list[AuditLogOut], dependencies=[AdminOnly])
def audit_logs(
    db: DbSession,
    actor_user_id: int | None = None,
    action: AuditAction | None = None,
    resource_type: str | None = None,
    limit: int = 200,
) -> list[AuditLogOut]:
    return [
        AuditLogOut.model_validate(entry)
        for entry in service.list_audit_logs(
            db,
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            limit=limit,
        )
    ]


# --- data subject rights ---------------------------------------------------


@router.get("/me/export", response_model=PersonalDataExport)
def export_my_data(db: DbSession, user=Depends(get_current_user)) -> PersonalDataExport:
    """DPDP right of access: everything held about you, in one response."""
    export = service.export_personal_data(db, user)
    service.audit(
        db,
        action=AuditAction.EXPORT,
        resource_type="personal_data",
        resource_id=user.id,
        actor_user_id=user.id,
        detail="Data principal exercised the right of access",
    )
    return export


@router.post(
    "/me/requests",
    response_model=DataSubjectRequestOut,
    status_code=http_status.HTTP_201_CREATED,
)
def create_request(
    payload: DataSubjectRequestCreate, db: DbSession, user=Depends(get_current_user)
) -> DataSubjectRequestOut:
    """Ask to see, correct, or erase your data."""
    request = service.create_request(
        db, payload.request_type, user_id=user.id, detail=payload.detail
    )
    return DataSubjectRequestOut.model_validate(request)


@router.get(
    "/requests", response_model=list[DataSubjectRequestOut], dependencies=[AdminOnly]
)
def list_requests(
    db: DbSession, request_status: RequestStatus | None = None
) -> list[DataSubjectRequestOut]:
    return [
        DataSubjectRequestOut.model_validate(r)
        for r in service.list_requests(db, status=request_status)
    ]


@router.post(
    "/requests/{request_id}/erase",
    response_model=ErasureResult,
    dependencies=[AdminOnly],
)
def perform_erasure(
    request_id: int, db: DbSession, user=Depends(get_current_user)
) -> ErasureResult:
    """Anonymise identifiers, keeping clinical records as retention rules require."""
    return service.perform_erasure(db, request_id, user.id)
