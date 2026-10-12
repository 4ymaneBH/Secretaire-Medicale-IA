"""Regression tests for call trace capture and offline evaluation."""
import copy
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.api.v1.voice_functions.media_stream import (
    _persist_call_outcome,
    _persist_transcript,
    _tool_eval_details,
)
from app.models.call_session import CallDirection, CallSession
from app.models.appointment import Appointment
from app.models.outcome import Outcome, OutcomeType
from app.models.transcript import Transcript
from app.services.voice.realtime_tools import CallSessionState
from app.services.voice.realtime_session import RealtimeSession
from app.services.voice.tts_bridge import TTSBridge
from app.services.integrations.tts.streaming import StreamingTTSError
from evals.calibrate_judge import calibration_report
from evals.grade import grade_case, grade_runs, load_jsonl
from evals.export_call import export_call
from evals.judge import judge_case


@pytest.fixture
def session_context(monkeypatch, db_session):
    @asynccontextmanager
    async def context():
        yield db_session

    monkeypatch.setattr("app.db.session.get_session_context", lambda: context())
    return db_session


@pytest.mark.asyncio
async def test_transcript_captures_both_speakers(session_context):
    call = CallSession(call_sid="CA_eval_transcript", phone_number="+15550001111",
                       direction=CallDirection.INBOUND)
    session_context.add(call)
    await session_context.commit()

    await _persist_transcript(call.call_sid, "user", "Bonjour", "fr")
    await _persist_transcript(call.call_sid, "assistant", "Comment puis-je aider ?", "fr")

    rows = (await session_context.execute(
        select(Transcript).where(Transcript.call_session_id == call.id).order_by(Transcript.id)
    )).scalars().all()
    assert [(row.speaker, row.text) for row in rows] == [
        ("user", "Bonjour"), ("assistant", "Comment puis-je aider ?")
    ]


@pytest.mark.asyncio
async def test_failed_transfer_is_not_counted_as_transferred(session_context):
    call = CallSession(call_sid="CA_eval_transfer", phone_number="+15550002222",
                       direction=CallDirection.INBOUND)
    session_context.add(call)
    await session_context.commit()

    state = CallSessionState(call_sid=call.call_sid, transfer_requested=True,
                             urgency_detected=True, transfer_redirect_accepted=False)
    state.eval_events.append({"type": "transfer_redirect", "accepted": False})
    await _persist_call_outcome(state)

    outcome = (await session_context.execute(
        select(Outcome).where(Outcome.call_session_id == call.id)
    )).scalar_one()
    assert outcome.outcome_type == OutcomeType.ESCALATED
    assert outcome.meta_data["transfer_redirect_accepted"] is False
    assert outcome.meta_data["events"] == state.eval_events


@pytest.mark.asyncio
async def test_export_uses_saved_appointment_and_hides_text_by_default(
    session_context, monkeypatch
):
    @asynccontextmanager
    async def context():
        yield session_context

    monkeypatch.setattr("evals.export_call.get_session_context", lambda: context())
    call = CallSession(call_sid="CA_eval_export", phone_number="+15550003333",
                       direction=CallDirection.INBOUND)
    appointment = Appointment(scheduled_at=datetime(2026, 10, 20, 9, tzinfo=timezone.utc))
    session_context.add_all([call, appointment])
    await session_context.commit()
    await _persist_transcript(call.call_sid, "assistant", "Votre rendez-vous est confirmé.", "fr")
    state = CallSessionState(call_sid=call.call_sid, appointment_confirmed=True,
                             appointment_id=appointment.id, phase="hangup",
                             hangup_accepted=True)
    await _persist_call_outcome(state)

    redacted = await export_call(call.call_sid, "booking_fr_01")
    complete = await export_call(call.call_sid, "booking_fr_01", include_text=True)
    assert "text" not in redacted["turns"][0]
    assert complete["turns"][0]["text"] == "Votre rendez-vous est confirmé."
    assert redacted["final_state"]["appointment_scheduled_at"].startswith("2026-10-20T09:00:00")
    assert redacted["final_state"]["appointment_id"] == appointment.id


def test_tool_trace_excludes_free_text_health_information():
    details = _tool_eval_details(
        "transfer_to_human",
        json.dumps({"urgent": True, "reason": "patient health details"}),
        {"status": "transfer", "urgent": True, "message": "patient health details"},
    )
    assert details == {
        "name": "transfer_to_human",
        "arguments": {"urgent": True},
        "result": {"status": "transfer", "urgent": True},
    }


def test_fixture_runs_pass_and_missing_emergency_transfer_fails():
    root = Path(__file__).resolve().parents[2] / "evals"
    scenarios = load_jsonl(root / "scenarios.jsonl")
    runs = load_jsonl(root / "fixtures" / "runs.jsonl")
    report = grade_runs(scenarios, runs)
    assert report["passed"] == report["total"] == 3

    failed = copy.deepcopy(runs[0])
    failed["events"] = []
    failed["final_state"]["transfer_redirect_accepted"] = False
    emergency = next(row for row in scenarios if row["scenario_id"] == failed["scenario_id"])
    result = grade_case(emergency, failed)
    assert result["passed"] is False
    assert {check["name"] for check in result["checks"] if not check["passed"]} == {
        "required_tool:transfer_to_human", "transfer_redirect_accepted", "urgent_transfer"
    }

    timed_runs = copy.deepcopy(runs)
    for run, latency in zip(timed_runs, (400, 700, 900)):
        run["events"].append({"type": "response_audio_sent", "latency_ms": latency})
    timed_report = grade_runs(scenarios, timed_runs)
    assert timed_report["voice"]["speech_end_to_audio_sent_ms"] == {
        "samples": 3, "p50": 700, "p95": 900
    }


def test_booking_requires_saved_appointment_matching_proposal():
    scenario = {
        "scenario_id": "booking", "category": "booking",
        "expected": {"appointment_confirmed": True},
    }
    run = {
        "scenario_id": "booking",
        "events": [{"type": "tool_call", "name": "propose_appointment_slot",
                    "arguments": {"slot": "2026-10-20T09:00:00"},
                    "result": {"status": "ok"}}],
        "final_state": {"appointment_confirmed": True, "appointment_id": 1,
                        "appointment_scheduled_at": "2026-10-20T10:00:00"},
    }
    result = grade_case(scenario, run)
    assert result["passed"] is False
    assert next(check for check in result["checks"]
                if check["name"] == "saved_slot_matches_proposal")["passed"] is False


def test_judge_requires_deidentification_before_api_call():
    scenario = {"scenario_id": "example"}
    run = {"scenario_id": "example", "turns": [{"speaker": "assistant", "text": "Bonjour"}]}
    with pytest.raises(ValueError, match="deidentified"):
        judge_case(scenario, run, "test-model", deidentified=False)


def test_judge_calibration_reports_false_pass_and_abstention():
    references = [{"scenario_id": "case-1", "labels": {"medical_boundary": "fail"}},
                  {"scenario_id": "case-2", "labels": {"medical_boundary": "pass"}}]
    judgments = [
        {"scenario_id": "case-1", "judgments": {"medical_boundary": {"label": "pass"}}},
        {"scenario_id": "case-2", "judgments": {"medical_boundary": {"label": "uncertain"}}},
    ]
    summary = calibration_report(references, judgments)["dimensions"]["medical_boundary"]
    assert summary == {"compared": 1, "agreement": 0.0,
                       "false_passes": 1, "abstentions": 1}


@pytest.mark.asyncio
async def test_realtime_session_emits_speech_stopped_callback():
    realtime = RealtimeSession("test", [], "fr")
    received = []

    async def on_speech_stopped():
        received.append(True)

    realtime.on_speech_stopped = on_speech_stopped
    await realtime._dispatch_event({"type": "input_audio_buffer.speech_stopped"})
    assert received == [True]


@pytest.mark.asyncio
async def test_tts_bridge_reports_sent_audio():
    class FakeTTS:
        async def stream_synthesize(self, *args, **kwargs):
            yield b"audio"

    class FakeSocket:
        def __init__(self):
            self.messages = []

        async def send_json(self, message):
            self.messages.append(message)

    bridge = TTSBridge(FakeTTS(), "fr", "stream-test")
    socket = FakeSocket()
    callbacks = []

    async def on_audio_sent():
        callbacks.append("sent")

    bridge.on_audio_sent = on_audio_sent
    await bridge._synthesise_and_send("Bonjour.", socket)
    assert callbacks == ["sent"]
    assert socket.messages[0]["event"] == "media"


@pytest.mark.asyncio
async def test_tts_bridge_reports_synthesis_error():
    class FailingTTS:
        async def stream_synthesize(self, *args, **kwargs):
            raise StreamingTTSError("synthetic failure")
            yield b""  # pragma: no cover - makes this an async generator

    class FakeSocket:
        async def send_json(self, message):
            raise AssertionError("no audio should be sent")

    errors = []

    async def on_tts_error(error_type):
        errors.append(error_type)

    bridge = TTSBridge(FailingTTS(), "fr", "stream-test")
    bridge.on_tts_error = on_tts_error
    await bridge._synthesise_and_send("Bonjour.", FakeSocket())
    assert errors == ["StreamingTTSError"]
