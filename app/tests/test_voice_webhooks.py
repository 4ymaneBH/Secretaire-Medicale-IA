"""Route-level tests for Twilio webhook security and outbound call persistence."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette.requests import Request

from app.api.v1 import voice
from app.api.v1.voice_functions.outbound_call import handle_outbound_call
from app.models.appointment import Appointment, AppointmentStatus
from app.models.call_session import CallDirection, CallSession, Patient
from app.services.voice.outbound_dispatcher import initiate_call_for_appointment


def _voice_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(voice.router, prefix="/api/v1")
    return app


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/api/v1/voice/inbound", {"CallSid": "CA_inbound", "From": "+15550001111"}),
        ("/api/v1/voice/outbound", {"CallSid": "CA_outbound"}),
        ("/api/v1/voice/status", {"CallSid": "CA_status", "CallStatus": "completed"}),
    ],
)
def test_twilio_http_webhooks_enforce_signature_dependency(path, data):
    app = _voice_test_app()

    async def reject_signature():
        raise HTTPException(status_code=401, detail="Invalid Twilio signature")

    app.dependency_overrides[voice.verify_twilio_signature] = reject_signature

    with TestClient(app) as client:
        response = client.post(path, data=data)

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid Twilio signature"


def test_twilio_inbound_webhook_enforces_rate_limit_dependency():
    app = _voice_test_app()

    async def allow_signature():
        return None

    async def reject_rate_limit():
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    app.dependency_overrides[voice.verify_twilio_signature] = allow_signature
    app.dependency_overrides[voice.rate_limit_twilio_voice] = reject_rate_limit

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/voice/inbound",
            data={"CallSid": "CA_limited", "From": "+15550001111"},
        )

    assert response.status_code == 429
    assert response.json()["detail"] == "Rate limit exceeded"


@pytest.mark.asyncio
async def test_outbound_call_session_links_patient_and_is_idempotent(monkeypatch, db_session):
    patient = Patient(phone_number="+15550002222", language_preference="fr")
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

    monkeypatch.setattr("app.db.session.get_session_context", lambda: session_context())

    request = Request({"type": "http", "method": "POST", "path": "/voice/outbound", "headers": []})
    kwargs = {
        "request": request,
        "call_sid": "CA_patient_link",
        "job_id": str(appointment.id),
        "purpose": "reminder",
    }

    first_response = await handle_outbound_call(**kwargs)
    second_response = await handle_outbound_call(**kwargs)

    result = await db_session.execute(
        select(CallSession).where(CallSession.call_sid == "CA_patient_link")
    )
    call_session = result.scalar_one()
    count = await db_session.scalar(
        select(func.count()).select_from(CallSession).where(
            CallSession.call_sid == "CA_patient_link"
        )
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert count == 1
    assert call_session.direction == CallDirection.OUTBOUND
    assert call_session.patient_id == patient.id
    assert call_session.appointment_id == appointment.id
    assert call_session.phone_number == patient.phone_number


@pytest.mark.asyncio
async def test_outbound_dispatcher_loads_patient_before_session_closes(monkeypatch, db_session):
    patient = Patient(phone_number="+15550003333", language_preference="fr")
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

    class FakeTelephonyProvider:
        def __init__(self):
            self.to_number = None

        async def initiate_outbound_call(
            self,
            to_number,
            callback_url,
            status_callback_url=None,
        ):
            self.to_number = to_number
            assert f"job_id={appointment.id}" in callback_url
            assert status_callback_url.endswith("/api/v1/voice/status")
            return "CA_dispatched"

    provider = FakeTelephonyProvider()
    monkeypatch.setattr(
        "app.services.voice.outbound_dispatcher.get_session_context",
        lambda: session_context(),
    )
    monkeypatch.setattr(
        "app.services.voice.outbound_dispatcher.get_telephony_provider",
        lambda: provider,
    )

    call_sid = await initiate_call_for_appointment(appointment.id, "reminder")

    assert call_sid == "CA_dispatched"
    assert provider.to_number == patient.phone_number
