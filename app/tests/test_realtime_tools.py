"""Conversation-quality scenario tests: scripted tool-call sequences an inbound/outbound
call is expected to trigger, per the CDC's fonctionnalités (booking, transfer, language switch).
"""
import json
from contextlib import asynccontextmanager

import pytest

from app.models.call_session import Patient
from app.services.voice.realtime_tools import CallSessionState, execute_tool


@pytest.fixture
async def booking_env(monkeypatch, db_session):
    """Route realtime_tools' `get_session_context()` to the in-memory test DB and
    return a persisted Patient to book appointments for."""
    @asynccontextmanager
    async def _cm():
        yield db_session

    monkeypatch.setattr("app.db.session.get_session_context", lambda: _cm())

    p = Patient(phone_number="+15550001111", language_preference="fr")
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


@pytest.mark.asyncio
async def test_book_appointment_scenario(booking_env):
    """Patient asks for a slot, the assistant proposes one, then confirms it."""
    session = CallSessionState(call_sid="CA_book", patient_id=booking_env.id)

    slots_result = json.loads(await execute_tool("t1", "get_available_slots", "{}", session))
    assert slots_result["status"] == "ok"
    assert len(slots_result["slots"]) > 0

    slot = slots_result["slots"][0]
    propose_result = json.loads(
        await execute_tool("t2", "propose_appointment_slot", json.dumps({"slot": slot}), session)
    )
    assert propose_result["status"] == "ok"
    assert session.proposed_slot == slot

    confirm_result = json.loads(
        await execute_tool(
            "t3", "confirm_appointment", json.dumps({"reason": "consultation générale"}), session
        )
    )
    assert confirm_result["status"] == "ok"
    assert session.appointment_confirmed is True
    assert session.appointment_id == confirm_result["appointment_id"]


@pytest.mark.asyncio
async def test_confirm_without_proposal_fails(booking_env):
    """Confirming with no prior slot proposal is rejected, not silently accepted."""
    session = CallSessionState(call_sid="CA_no_slot", patient_id=booking_env.id)

    result = json.loads(await execute_tool("t1", "confirm_appointment", "{}", session))
    assert result["status"] == "error"
    assert session.appointment_confirmed is False


@pytest.mark.asyncio
async def test_urgent_transfer_scenario():
    """Patient describes an emergency; the model must signal an immediate transfer."""
    session = CallSessionState(call_sid="CA_urgent")

    result = json.loads(
        await execute_tool(
            "t1",
            "transfer_to_human",
            json.dumps({"reason": "chest pain", "urgent": True}),
            session,
        )
    )
    assert result["status"] == "transfer"
    assert result["urgent"] is True
    assert session.transfer_requested is True
    assert session.urgency_detected is True


@pytest.mark.asyncio
async def test_language_switch_scenario():
    """Patient switches from French to English mid-call."""
    session = CallSessionState(call_sid="CA_lang", language="fr")

    result = json.loads(
        await execute_tool("t1", "switch_language", json.dumps({"language": "en"}), session)
    )
    assert result["status"] == "language_switched"
    assert session.language == "en"


@pytest.mark.asyncio
async def test_language_switch_rejects_unsupported_language():
    session = CallSessionState(call_sid="CA_lang_bad", language="fr")

    result = json.loads(
        await execute_tool("t1", "switch_language", json.dumps({"language": "de"}), session)
    )
    assert result["status"] == "error"
    assert session.language == "fr"  # unchanged


@pytest.mark.asyncio
async def test_end_call_scenario():
    """A normal call close: the assistant signals hangup and leaves a summary."""
    session = CallSessionState(call_sid="CA_end")

    result = json.loads(
        await execute_tool("t1", "end_call", json.dumps({"reason": "patient satisfied"}), session)
    )
    assert result["status"] == "hangup"
    assert session.phase == "hangup"
    assert session.summary is not None
