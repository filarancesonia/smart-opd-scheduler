"""Room 9 gateway clients.

Each client runs in one of two modes:

  live  credentials are configured, so real HTTP calls are made
  mock  credentials are absent, so a deterministic offline stub answers

Mock responses are always stamped ``is_mock: True`` and that flag is carried
into the database and out through the API. A demo that cannot be told apart
from a real integration is a demo that will eventually be mistaken for one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import UpstreamError
from app.modules.integration.verhoeff import digits_only, is_valid

#: Network calls are bounded — a slow government gateway must not hold an OPD
#: counter hostage.
TIMEOUT_SECONDS = 10.0


@dataclass
class GatewayResponse:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    is_mock: bool = False
    error: str = ""
    status_code: int | None = None


def _stable_hash(value: str, length: int = 12) -> str:
    """Deterministic pseudo-id, so mock runs are reproducible."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


class _BaseClient:
    system = "base"

    @property
    def is_live(self) -> bool:  # pragma: no cover - overridden
        return False

    @property
    def mode(self) -> str:
        return "live" if self.is_live else "mock"

    def _post(self, url: str, payload: dict, headers: dict) -> GatewayResponse:
        try:
            response = httpx.post(
                url, json=payload, headers=headers, timeout=TIMEOUT_SECONDS
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"{self.system} gateway unreachable: {exc}") from exc

        if response.status_code >= 400:
            return GatewayResponse(
                ok=False,
                error=response.text[:500],
                status_code=response.status_code,
            )
        try:
            data = response.json()
        except json.JSONDecodeError:
            data = {"raw": response.text[:500]}
        return GatewayResponse(ok=True, data=data, status_code=response.status_code)


class AbhaClient(_BaseClient):
    """Ayushman Bharat Health Account — the national health ID."""

    system = "abha"

    @property
    def is_live(self) -> bool:
        return bool(settings.abha_client_id and settings.abha_client_secret)

    def verify_number(self, abha_number: str) -> GatewayResponse:
        """Confirm an ABHA number exists and belongs to a live account."""
        digits = digits_only(abha_number)
        if len(digits) != 14:
            return GatewayResponse(
                ok=False, error="An ABHA number has 14 digits", is_mock=not self.is_live
            )
        if not is_valid(digits):
            # Caught locally, at the counter, in front of the patient.
            return GatewayResponse(
                ok=False,
                error="ABHA number failed its check digit — please re-enter",
                is_mock=not self.is_live,
            )

        if not self.is_live:
            return GatewayResponse(
                ok=True,
                is_mock=True,
                data={
                    "abhaNumber": digits,
                    "abhaAddress": f"user{digits[-4:]}@abdm",
                    "status": "ACTIVE",
                    "verificationMethod": "mock",
                },
            )

        return self._post(
            f"{settings.abha_base_url}/v1/account/verify",
            {"abhaNumber": digits},
            self._headers(),
        )

    def _headers(self) -> dict:
        return {
            "X-CM-ID": "sbx",
            "Authorization": f"Bearer {settings.abha_client_secret}",
            "Content-Type": "application/json",
        }


class OrsClient(_BaseClient):
    """Online Registration System — the national appointment portal."""

    system = "ors"

    @property
    def is_live(self) -> bool:
        return bool(settings.ors_api_key)

    def push_appointment(self, payload: dict) -> GatewayResponse:
        if not self.is_live:
            reference = _stable_hash(payload.get("bookingReference", ""))
            return GatewayResponse(
                ok=True,
                is_mock=True,
                data={"orsReferenceId": f"ORS-{reference.upper()}", "status": "BOOKED"},
            )
        return self._post(
            f"{settings.ors_base_url}/v1/appointments",
            payload,
            {"X-API-Key": settings.ors_api_key, "Content-Type": "application/json"},
        )

    def cancel_appointment(self, ors_reference: str) -> GatewayResponse:
        if not self.is_live:
            return GatewayResponse(
                ok=True,
                is_mock=True,
                data={"orsReferenceId": ors_reference, "status": "CANCELLED"},
            )
        return self._post(
            f"{settings.ors_base_url}/v1/appointments/{ors_reference}/cancel",
            {},
            {"X-API-Key": settings.ors_api_key},
        )

    def fetch_appointments(self, facility_id: str, on_date: date) -> GatewayResponse:
        """Pull bookings made on the portal rather than through us."""
        if not self.is_live:
            return GatewayResponse(ok=True, is_mock=True, data={"appointments": []})
        try:
            response = httpx.get(
                f"{settings.ors_base_url}/v1/facilities/{facility_id}/appointments",
                params={"date": on_date.isoformat()},
                headers={"X-API-Key": settings.ors_api_key},
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"ORS gateway unreachable: {exc}") from exc
        if response.status_code >= 400:
            return GatewayResponse(ok=False, error=response.text[:500])
        return GatewayResponse(ok=True, data=response.json())


class FhirClient(_BaseClient):
    """The hospital's own HMIS / EHR, over FHIR R4."""

    system = "hmis"

    @property
    def is_live(self) -> bool:
        return bool(settings.hmis_fhir_base_url and settings.hmis_fhir_token)

    def push_encounter(self, bundle: dict) -> GatewayResponse:
        if not self.is_live:
            return GatewayResponse(
                ok=True,
                is_mock=True,
                data={
                    "resourceType": "Bundle",
                    "id": _stable_hash(json.dumps(bundle, sort_keys=True)),
                    "type": "transaction-response",
                },
            )
        return self._post(
            f"{settings.hmis_fhir_base_url.rstrip('/')}/",
            bundle,
            {
                "Authorization": f"Bearer {settings.hmis_fhir_token}",
                "Content-Type": "application/fhir+json",
            },
        )


# --- FHIR resource building ------------------------------------------------


def build_patient_resource(patient, abha_number: str | None = None) -> dict:
    """A FHIR R4 Patient. Only what the encounter genuinely needs."""
    identifiers = [
        {
            "system": "https://opd.gov.in/patient-id",
            "value": str(patient.id),
        }
    ]
    if abha_number:
        identifiers.append(
            {"system": "https://healthid.abdm.gov.in/", "value": abha_number}
        )

    resource: dict[str, Any] = {
        "resourceType": "Patient",
        "id": f"patient-{patient.id}",
        "identifier": identifiers,
        "name": [{"text": patient.full_name}],
        "telecom": [{"system": "phone", "value": patient.phone, "use": "mobile"}],
    }
    if patient.gender:
        resource["gender"] = patient.gender
    return resource


def build_encounter_bundle(
    *,
    patient,
    appointment,
    doctor_name: str,
    department_name: str,
    abha_number: str | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> dict:
    """A FHIR transaction Bundle carrying one OPD encounter."""
    period: dict[str, str] = {}
    if started_at:
        period["start"] = started_at.astimezone(timezone.utc).isoformat()
    if ended_at:
        period["end"] = ended_at.astimezone(timezone.utc).isoformat()

    encounter = {
        "resourceType": "Encounter",
        "id": f"encounter-{appointment.id}",
        "status": "finished"
        if appointment.status == "completed"
        else "in-progress",
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "AMB",
            "display": "ambulatory",
        },
        "subject": {"reference": f"Patient/patient-{patient.id}"},
        "participant": [{"individual": {"display": doctor_name}}],
        "serviceType": {"text": department_name},
        "identifier": [
            {
                "system": "https://opd.gov.in/booking-reference",
                "value": appointment.booking_reference,
            }
        ],
        "location": [{"location": {"display": appointment.room}}],
    }
    if period:
        encounter["period"] = period
    if appointment.reason:
        encounter["reasonCode"] = [{"text": appointment.reason}]

    return {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {
                "resource": build_patient_resource(patient, abha_number),
                "request": {"method": "PUT", "url": f"Patient/patient-{patient.id}"},
            },
            {
                "resource": encounter,
                "request": {
                    "method": "PUT",
                    "url": f"Encounter/encounter-{appointment.id}",
                },
            },
        ],
    }


abha_client = AbhaClient()
ors_client = OrsClient()
fhir_client = FhirClient()
