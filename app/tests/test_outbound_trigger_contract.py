"""Contract tests for POST /api/v1/voice/outbound/trigger/{appointment_id}.

This is the only route Module 1 (central backend) calls, so its auth, its error
codes and its success payload are a published contract. These tests pin them.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import voice
from app.core.config import settings
from app.models.appointment import Appointment, AppointmentStatus
from app.models.call_session import Patient
from app.services.voice import outbound_dispatcher
from app.services.voice.outbound_dispatcher import (
    AppointmentNotFoundError,
    InvalidPurposeError,
    PatientNotCallableError,
    TelephonyProviderError,
    initiate_call_for_appointment,
)

# Read the configured key rather than hardcoding conftest's default: a developer
# with INTERNAL_API_KEY exported (or a .env present) would otherwise see these
# tests fail for a reason that has nothing to do with the contract.
INTERNAL_KEY = settings.internal_api_key
TRIGGER_PATH = "/api/v1/voice/outbound/trigger/1"


def _client(monkeypatch, dispatcher):
    """Build a test client whose trigger route uses `dispatcher` and skips rate limiting."""
    monkeypatch.setattr(
        "app.services.voice.outbound_dispatcher.initiate_call_for_appointment",
        dispatcher,
    )

    app = FastAPI()
    app.include_router(voice.router, prefix="/api/v1")

    async def no_rate_limit():
        return None

    app.dependency_overrides[voice.rate_limit_internal_trigger] = no_rate_limit
    return TestClient(app)


async def _succeeds(appointment_id, purpose):
    return "CA_contract_test"


# ---------------------------------------------------------------------------
# Internal API key
# ---------------------------------------------------------------------------

def test_valid_internal_key_is_accepted(monkeypatch):
    with _client(monkeypatch, _succeeds) as client:
        response = client.post(TRIGGER_PATH, headers={"X-Internal-API-Key": INTERNAL_KEY})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="missing"),
        pytest.param({"X-Internal-API-Key": "wrong-key"}, id="wrong"),
        pytest.param({"X-Internal-API-Key": ""}, id="empty"),
    ],
)
def test_bad_internal_key_is_rejected(monkeypatch, headers):
    called = False

    async def dispatcher(appointment_id, purpose):
        nonlocal called
        called = True
        return "CA_should_not_happen"

    with _client(monkeypatch, dispatcher) as client:
        response = client.post(TRIGGER_PATH, headers=headers)

    assert response.status_code == 401
    assert called is False, "no call may be placed without a valid internal key"


def test_rejected_key_is_not_echoed_back(monkeypatch):
    with _client(monkeypatch, _succeeds) as client:
        response = client.post(TRIGGER_PATH, headers={"X-Internal-API-Key": "super-secret-wrong"})

    assert "super-secret-wrong" not in response.text
    assert INTERNAL_KEY not in response.text


# ---------------------------------------------------------------------------
# Success payload
# ---------------------------------------------------------------------------

def test_success_returns_stable_payload(monkeypatch):
    with _client(monkeypatch, _succeeds) as client:
        response = client.post(
            TRIGGER_PATH + "?purpose=confirmation",
            headers={"X-Internal-API-Key": INTERNAL_KEY},
        )

    assert response.status_code == 200
    assert response.json() == {"success": True, "call_sid": "CA_contract_test"}


def test_purpose_is_forwarded_to_the_dispatcher(monkeypatch):
    seen = {}

    async def dispatcher(appointment_id, purpose):
        seen["appointment_id"] = appointment_id
        seen["purpose"] = purpose
        return "CA_forwarded"

    with _client(monkeypatch, dispatcher) as client:
        client.post(
            "/api/v1/voice/outbound/trigger/42?purpose=satisfaction_survey",
            headers={"X-Internal-API-Key": INTERNAL_KEY},
        )

    assert seen == {"appointment_id": 42, "purpose": "satisfaction_survey"}


# ---------------------------------------------------------------------------
# Error mapping (the table Module 1 codes against)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (InvalidPurposeError("bad purpose"), 400),
        (AppointmentNotFoundError("no such appointment"), 404),
        (PatientNotCallableError("no phone number"), 409),
        (TelephonyProviderError("twilio refused"), 502),
    ],
)
def test_dispatcher_errors_map_to_documented_status_codes(monkeypatch, error, expected_status):
    async def dispatcher(appointment_id, purpose):
        raise error

    with _client(monkeypatch, dispatcher) as client:
        response = client.post(TRIGGER_PATH, headers={"X-Internal-API-Key": INTERNAL_KEY})

    assert response.status_code == expected_status


# ---------------------------------------------------------------------------
# Rate limit must not be caller-controllable
# ---------------------------------------------------------------------------

def test_rate_limit_internals_are_not_query_parameters():
    """`Depends(rate_limit)` would expose limit/window_seconds as query params,
    letting any caller raise their own limit."""
    app = FastAPI()
    app.include_router(voice.router, prefix="/api/v1")

    spec = app.openapi()
    operation = spec["paths"]["/api/v1/voice/outbound/trigger/{appointment_id}"]["post"]
    query_params = {p["name"] for p in operation.get("parameters", []) if p["in"] == "query"}

    assert query_params == {"purpose"}


def test_trigger_route_documents_purpose_and_error_codes():
    app = FastAPI()
    app.include_router(voice.router, prefix="/api/v1")

    operation = app.openapi()["paths"]["/api/v1/voice/outbound/trigger/{appointment_id}"]["post"]

    purpose = next(p for p in operation["parameters"] if p["name"] == "purpose")
    assert set(purpose["schema"]["enum"]) == outbound_dispatcher.OUTBOUND_PURPOSES
    assert {"200", "400", "401", "404", "409", "502"} <= set(operation["responses"])


def test_uncallable_appointment_is_distinct_from_a_malformed_appointment_id(monkeypatch):
    """FastAPI returns 422 for a non-integer appointment_id. A callable-but-unreachable
    appointment must not reuse that code, or Module 1 cannot tell them apart."""
    async def dispatcher(appointment_id, purpose):
        raise PatientNotCallableError("no phone number")

    with _client(monkeypatch, dispatcher) as client:
        uncallable = client.post(TRIGGER_PATH, headers={"X-Internal-API-Key": INTERNAL_KEY})
        malformed = client.post(
            "/api/v1/voice/outbound/trigger/abc",
            headers={"X-Internal-API-Key": INTERNAL_KEY},
        )

    assert uncallable.status_code == 409
    assert malformed.status_code == 422


# ---------------------------------------------------------------------------
# Dispatcher-level guarantees
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_purpose_never_places_a_call(monkeypatch):
    """An unrecognised purpose must not silently fall back to 'reminder' — that
    would read the wrong script to a real patient."""
    class ExplodingProvider:
        async def initiate_outbound_call(self, **kwargs):
            raise AssertionError("no call may be placed for an invalid purpose")

    monkeypatch.setattr(
        "app.services.voice.outbound_dispatcher.get_telephony_provider",
        lambda: ExplodingProvider(),
    )

    with pytest.raises(InvalidPurposeError):
        await initiate_call_for_appointment(1, "not_a_real_purpose")


@pytest.mark.asyncio
async def test_missing_appointment_raises_not_found(monkeypatch, db_session):
    @asynccontextmanager
    async def session_context():
        yield db_session

    monkeypatch.setattr(
        "app.services.voice.outbound_dispatcher.get_session_context",
        lambda: session_context(),
    )

    with pytest.raises(AppointmentNotFoundError):
        await initiate_call_for_appointment(9999, "reminder")


@pytest.mark.asyncio
async def test_appointment_without_patient_places_no_call(monkeypatch, db_session):
    appointment = Appointment(
        patient_id=None,
        scheduled_at=datetime.now(timezone.utc),
        status=AppointmentStatus.CONFIRMED,
    )
    db_session.add(appointment)
    await db_session.commit()
    await db_session.refresh(appointment)

    @asynccontextmanager
    async def session_context():
        yield db_session

    class ExplodingProvider:
        async def initiate_outbound_call(self, **kwargs):
            raise AssertionError("no call may be placed without a patient phone number")

    monkeypatch.setattr(
        "app.services.voice.outbound_dispatcher.get_session_context",
        lambda: session_context(),
    )
    monkeypatch.setattr(
        "app.services.voice.outbound_dispatcher.get_telephony_provider",
        lambda: ExplodingProvider(),
    )

    with pytest.raises(PatientNotCallableError):
        await initiate_call_for_appointment(appointment.id, "reminder")


@pytest.mark.asyncio
async def test_provider_failure_raises_telephony_error(monkeypatch, db_session):
    patient = Patient(phone_number="+15550004444", language_preference="fr")
    db_session.add(patient)
    await db_session.flush()

    appointment = Appointment(
        patient_id=patient.id,
        scheduled_at=datetime.now(timezone.utc),
        status=AppointmentStatus.CONFIRMED,
    )
    db_session.add(appointment)
    await db_session.commit()
    await db_session.refresh(appointment)

    @asynccontextmanager
    async def session_context():
        yield db_session

    class FailingProvider:
        async def initiate_outbound_call(self, **kwargs):
            return None

    monkeypatch.setattr(
        "app.services.voice.outbound_dispatcher.get_session_context",
        lambda: session_context(),
    )
    monkeypatch.setattr(
        "app.services.voice.outbound_dispatcher.get_telephony_provider",
        lambda: FailingProvider(),
    )

    with pytest.raises(TelephonyProviderError):
        await initiate_call_for_appointment(appointment.id, "reminder")
