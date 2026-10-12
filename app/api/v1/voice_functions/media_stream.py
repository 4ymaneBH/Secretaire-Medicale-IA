"""WebSocket handler for the Twilio Media Stream <-> OpenAI Realtime API bridge.

Architecture
------------
Twilio Media Stream (WebSocket)
    <->  base64 mulaw audio frames
FastAPI /ws/media-stream
    <->  text deltas
  OpenAI Realtime API (WebSocket)
    <->  synthesised audio chunks
  ElevenLabs Streaming TTS
    <->  base64 audio frames back
Twilio Media Stream

Three concurrent asyncio tasks run for the lifetime of every call:

1. ``receive_from_twilio``   — reads Twilio "media" frames, forwards audio to
                               OpenAI Realtime; also handles the "stop" event.
2. ``realtime.receive_loop`` — reads OpenAI events, dispatches to callbacks
                               (text deltas -> TTSBridge, tool calls -> executor,
                               speech-started -> barge-in interrupt).
3. ``bridge.tts_consumer``   — reads sentences from TTSBridge's internal queue,
                               synthesises via ElevenLabs, streams audio back to
                               Twilio.
"""

import asyncio
import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket
from fastapi.websockets import WebSocketDisconnect

from app.core.logging_config import get_logger
from app.services.voice.realtime_session import (
    RealtimeConnectionError,
    RealtimeSession,
)
from app.services.voice.tts_bridge import TTSBridge
from app.services.voice.realtime_tools import execute_tool, CallSessionState
from app.services.voice.realtime_prompts import build_system_prompt, get_tools
from app.services.integrations.tts.streaming import get_streaming_tts

logger = get_logger(__name__)

_LOG_PREFIX = "[MediaStream]"

# Maximum call duration in seconds - kill-switch to prevent runaway billing.
MAX_CALL_DURATION_SECONDS = 420  # 7 minutes

# Only record structured, non-free-text tool fields. Reasons and recall queries
# can contain patient health information and are already represented in the
# access-controlled conversation transcript when the patient says them.
_EVAL_TOOL_ARGUMENTS = {
    "get_available_slots": {"days_ahead"},
    "propose_appointment_slot": {"slot"},
    "confirm_appointment": set(),
    "transfer_to_human": {"urgent"},
    "switch_language": {"language"},
    "recall_context": set(),
    "end_call": set(),
}
_EVAL_TOOL_RESULTS = {"status", "slots", "appointment_id", "language", "urgent"}

router = APIRouter()


def _record_eval_event(session: CallSessionState, event_type: str, **details: object) -> None:
    session.eval_events.append({
        "type": event_type,
        "at": datetime.now(timezone.utc).isoformat(),
        **details,
    })


def _tool_eval_details(name: str, args_json: str, result: dict) -> dict:
    """Keep fields needed for grading without duplicating free-text health data."""
    try:
        args = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    allowed_args = _EVAL_TOOL_ARGUMENTS.get(name, set())
    return {
        "name": name,
        "arguments": {key: args[key] for key in allowed_args if key in args},
        "result": {key: result[key] for key in _EVAL_TOOL_RESULTS if key in result},
    }


async def _persist_transcript(call_sid: str, speaker: str, text: str, language: str) -> None:
    """Persist one complete turn; failures must not interrupt a live call."""
    if not text.strip():
        return
    try:
        from sqlalchemy import select
        from app.db.session import get_session_context
        from app.models.call_session import CallSession
        from app.models.transcript import Transcript

        async with get_session_context() as db:
            result = await db.execute(select(CallSession.id).where(CallSession.call_sid == call_sid))
            call_session_id = result.scalar_one_or_none()
            if call_session_id is None:
                return
            db.add(Transcript(
                call_session_id=call_session_id,
                speaker=speaker,
                text=text.strip(),
                language=language,
                timestamp=datetime.now(timezone.utc),
            ))
            await db.commit()
    except Exception as exc:
        logger.error(f"{_LOG_PREFIX} Failed to persist {speaker} transcript: {exc}", exc_info=True)


def _twilio_client():
    from twilio.rest import Client as TwilioClient
    from app.core.config import get_settings

    settings = get_settings()
    return TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)


async def _force_hangup_twilio(call_sid: str) -> bool:
    """Terminate a Twilio PSTN call via REST API.

    Closing the WebSocket alone does not hang up the phone — Twilio keeps
    the call alive and continues billing until the call is explicitly
    marked completed via the REST API.
    """
    client = _twilio_client()

    for attempt in range(1, 4):
        try:
            client.calls(call_sid).update(status="completed")
            logger.info(f"{_LOG_PREFIX} Twilio call {call_sid!r} terminated (attempt {attempt})")
            return True
        except Exception as exc:
            logger.warning(f"{_LOG_PREFIX} Hangup attempt {attempt}/3 failed for {call_sid!r}: {exc}")
            if attempt < 3:
                await asyncio.sleep(1.0)

    logger.error(f"{_LOG_PREFIX} All hangup attempts failed for {call_sid!r}")
    return False


async def _transfer_call_to_human(call_sid: str) -> bool:
    """Redirect an in-progress Twilio call to a human secretary/practitioner.

    Ends the Media Stream (the current TwiML <Connect>) and replaces it with
    a <Dial> to the configured human transfer number.
    """
    from twilio.twiml.voice_response import VoiceResponse
    from app.core.config import get_settings

    settings = get_settings()
    twiml = VoiceResponse()
    twiml.dial(settings.human_transfer_number)

    client = _twilio_client()
    try:
        client.calls(call_sid).update(twiml=str(twiml))
        logger.info(f"{_LOG_PREFIX} Call {call_sid!r} transferred to human ({settings.human_transfer_number})")
        return True
    except Exception as exc:
        logger.error(f"{_LOG_PREFIX} Failed to transfer call {call_sid!r} to human: {exc}")
        return False


async def _persist_call_outcome(session: CallSessionState) -> None:
    """Save the final call outcome to the database."""
    try:
        from sqlalchemy import select
        from app.db.session import get_session_context
        from app.models.call_session import CallSession
        from app.models.outcome import Outcome, OutcomeType, TransferReason

        async with get_session_context() as db:
            result = await db.execute(
                select(CallSession).where(CallSession.call_sid == session.call_sid)
            )
            call_session = result.scalar_one_or_none()
            if not call_session:
                logger.warning(f"{_LOG_PREFIX} No CallSession found for {session.call_sid!r}")
                return

            if session.transfer_requested:
                # A successful Twilio redirect is observable here; whether a
                # human answered the forwarded leg is not.
                outcome_type = (
                    OutcomeType.TRANSFERRED if session.transfer_redirect_accepted
                    else OutcomeType.ESCALATED
                )
            elif session.phase == "hangup":
                outcome_type = (
                    OutcomeType.RESOLVED if session.hangup_accepted
                    else OutcomeType.FAILED
                )
            else:
                outcome_type = OutcomeType.ABANDONED
            outcome = Outcome(
                call_session_id=call_session.id,
                outcome_type=outcome_type,
                outcome_details=session.summary,
                issue_resolved="yes" if session.appointment_confirmed else "unknown",
                transfer_reason=TransferReason.URGENT_KEYWORD if session.urgency_detected else (
                    TransferReason.EXPLICIT_REQUEST if session.transfer_requested else None
                ),
                meta_data={
                    "eval_trace_version": 1,
                    "events": session.eval_events,
                    "appointment_confirmed": session.appointment_confirmed,
                    "appointment_id": session.appointment_id,
                    "transfer_requested": session.transfer_requested,
                    "transfer_redirect_accepted": session.transfer_redirect_accepted,
                    "hangup_accepted": session.hangup_accepted,
                },
            )
            db.add(outcome)
            await db.commit()
            logger.info(f"{_LOG_PREFIX} Outcome persisted for {session.call_sid!r}")

        if session.patient_id and session.summary:
            from app.services.ai.memory_service import get_memory_service
            await get_memory_service().store_memory(session.patient_id, session.summary)

    except Exception as exc:
        logger.error(f"{_LOG_PREFIX} Failed to persist call outcome: {exc}", exc_info=True)


async def _get_patient_id_for_call(call_sid: str) -> int | None:
    """Look up the patient linked to this call (set at CallSession creation)."""
    from sqlalchemy import select
    from app.db.session import get_session_context
    from app.models.call_session import CallSession

    async with get_session_context() as db:
        result = await db.execute(
            select(CallSession).where(CallSession.call_sid == call_sid)
        )
        call_session = result.scalar_one_or_none()
        return call_session.patient_id if call_session else None


async def _get_memory_context(patient_id: int, language: str) -> str | None:
    """Fetch a short summary of this patient's previous calls, if any."""
    from app.services.ai.memory_service import get_memory_service

    return await get_memory_service().get_recent_context(patient_id)


@router.websocket("/ws/media-stream")
async def media_stream_handler(websocket: WebSocket) -> None:
    """Handle a Twilio Media Stream WebSocket for one call (inbound or outbound)."""

    await websocket.accept()
    logger.info(f"{_LOG_PREFIX} WebSocket accepted - waiting for Twilio start event")

    stream_sid: str = ""
    call_sid: str = ""
    language: str = "fr"
    appointment_id: str | None = None
    purpose: str | None = None

    try:
        async for raw in websocket.iter_text():
            data = json.loads(raw)
            event = data.get("event")

            if event == "connected":
                continue

            if event == "start":
                start_payload = data.get("start", {})
                stream_sid = start_payload.get("streamSid", "")
                call_sid = start_payload.get("callSid", "")

                custom = start_payload.get("customParameters") or {}
                language = custom.get("language", "fr")
                appointment_id = custom.get("appointment_id")
                purpose = custom.get("purpose")

                logger.info(
                    f"{_LOG_PREFIX} Start event - call_sid={call_sid!r} "
                    f"stream_sid={stream_sid!r} language={language!r}"
                )
                break

            logger.debug(f"{_LOG_PREFIX} Pre-start event ignored: {event!r}")

    except WebSocketDisconnect:
        logger.warning(f"{_LOG_PREFIX} WebSocket disconnected before start event")
        return
    except Exception as exc:
        logger.error(f"{_LOG_PREFIX} Error while waiting for start event: {exc}", exc_info=True)
        await websocket.close()
        return

    if not call_sid:
        logger.error(f"{_LOG_PREFIX} No call_sid received in start event - closing")
        await websocket.close()
        return

    stream_start_monotonic = time.perf_counter()
    last_speech_stop_monotonic: float | None = None
    first_stream_audio_sent = False

    patient_id = await _get_patient_id_for_call(call_sid)
    session = CallSessionState(call_sid=call_sid, language=language, patient_id=patient_id)

    memory_context = await _get_memory_context(patient_id, language) if patient_id else None
    system_prompt = build_system_prompt(
        language, appointment_id=appointment_id, purpose=purpose, memory_context=memory_context
    )
    tools = get_tools(language)

    realtime = RealtimeSession(
        system_prompt=system_prompt,
        tools=tools,
        language=language,
        temperature=0.8,
    )

    tts = get_streaming_tts()
    bridge = TTSBridge(tts=tts, language=language, stream_sid=stream_sid)
    asyncio.create_task(tts.warmup())

    async def on_audio_sent() -> None:
        nonlocal first_stream_audio_sent, last_speech_stop_monotonic
        now = time.perf_counter()
        if not first_stream_audio_sent:
            first_stream_audio_sent = True
            _record_eval_event(
                session, "first_stream_audio_sent",
                latency_ms=round((now - stream_start_monotonic) * 1000),
            )
        if last_speech_stop_monotonic is not None:
            _record_eval_event(
                session, "response_audio_sent",
                latency_ms=round((now - last_speech_stop_monotonic) * 1000),
            )
            last_speech_stop_monotonic = None

    async def on_tts_error(error_type: str) -> None:
        _record_eval_event(session, "tts_error", error_type=error_type)

    bridge.on_audio_sent = on_audio_sent
    bridge.on_tts_error = on_tts_error

    realtime.on_text_delta = bridge.on_text_delta

    async def on_text_done(full_text: str) -> None:
        await bridge.on_text_done(full_text)
        await _persist_transcript(call_sid, "assistant", full_text, session.language)

    realtime.on_text_done = on_text_done

    async def on_speech_started() -> None:
        """Barge-in: user started speaking - interrupt in-flight TTS output."""
        logger.info(f"{_LOG_PREFIX} Barge-in detected for {call_sid!r}")
        _record_eval_event(session, "speech_started")
        bridge.reset_for_new_turn()
        await bridge.interrupt(websocket)

    realtime.on_speech_started = on_speech_started

    async def on_speech_stopped() -> None:
        nonlocal last_speech_stop_monotonic
        last_speech_stop_monotonic = time.perf_counter()
        _record_eval_event(session, "speech_stopped")

    realtime.on_speech_stopped = on_speech_stopped

    async def on_tool_call(tool_call_id: str, name: str, args_json: str) -> None:
        logger.info(f"{_LOG_PREFIX} Tool call received: name={name!r}")

        started_at = time.perf_counter()
        result_json = await execute_tool(tool_call_id, name, args_json, session)
        result = json.loads(result_json)
        status = result.get("status")
        _record_eval_event(
            session,
            "tool_call",
            **_tool_eval_details(name, args_json, result),
            duration_ms=round((time.perf_counter() - started_at) * 1000),
        )

        # end_call and transfer_to_human both end the audio bridge — the model
        # should not keep talking after either, so we suppress response.create.
        # language_switched also suppresses it here: the session must be
        # reconfigured for the new language *before* the next response is
        # generated, otherwise the reply comes back in the old language.
        trigger_response = status not in ("hangup", "transfer", "language_switched")
        await realtime.send_tool_result(tool_call_id, result_json, trigger_response=trigger_response)

        if status == "language_switched":
            new_language = result.get("language", session.language)
            await realtime.update_language(new_language)
            bridge.set_language(new_language)
            await realtime.trigger_initial_response()
            return

        if status == "hangup":
            logger.info(f"{_LOG_PREFIX} Hangup sentinel received - waiting for TTS to finish")
            await bridge.wait_until_drained(timeout=15.0)
            await asyncio.sleep(1.0)
            session.hangup_accepted = await _force_hangup_twilio(call_sid)
            _record_eval_event(session, "hangup", accepted=session.hangup_accepted)
            try:
                await websocket.close()
            except Exception:
                pass

        elif status == "transfer":
            logger.info(f"{_LOG_PREFIX} Transfer sentinel received - waiting for TTS to finish")
            await bridge.wait_until_drained(timeout=15.0)
            await asyncio.sleep(1.0)
            session.transfer_redirect_accepted = await _transfer_call_to_human(call_sid)
            _record_eval_event(
                session, "transfer_redirect", accepted=session.transfer_redirect_accepted
            )
            try:
                await websocket.close()
            except Exception:
                pass

    realtime.on_tool_call = on_tool_call

    async def on_transcript(text: str) -> None:
        """Persist the patient's transcript turn; request clarification on junk audio."""
        cleaned = text.strip()

        is_junk = (
            not cleaned
            or len(cleaned) < 2
            or cleaned in ("[SILENCE]", "[INAUDIBLE]", "...", ".")
            or all(c in ".,!?-– \n\t" for c in cleaned)
        )

        if is_junk:
            logger.info(f"{_LOG_PREFIX} Low-quality transcript - requesting clarification")
            clarify = {
                "fr": "[SYSTEM: L'audio était inaudible. Demande poliment de répéter.]",
                "en": "[SYSTEM: Audio was unclear. Politely ask the patient to repeat.]",
            }.get(session.language, "[SYSTEM: Audio was unclear. Politely ask the patient to repeat.]")
            await realtime.inject_user_turn(clarify)
            return

        await _persist_transcript(call_sid, "user", cleaned, session.language)

    realtime.on_transcript = on_transcript

    async def on_error(error_dict: dict) -> None:
        logger.error(f"{_LOG_PREFIX} OpenAI Realtime error: {error_dict}")

    realtime.on_error = on_error

    try:
        await realtime.connect()
    except RealtimeConnectionError as exc:
        logger.error(f"{_LOG_PREFIX} Failed to connect to OpenAI Realtime API: {exc}")
        await websocket.close()
        return

    logger.info(f"{_LOG_PREFIX} OpenAI Realtime connection established for {call_sid!r}")

    async def receive_from_twilio() -> None:
        """Forward Twilio audio frames to OpenAI Realtime and handle stop."""
        try:
            async for raw_message in websocket.iter_text():
                data = json.loads(raw_message)
                event_type = data.get("event")

                if event_type == "media":
                    await realtime.send_audio(data["media"]["payload"])
                elif event_type == "stop":
                    logger.info(f"{_LOG_PREFIX} Twilio 'stop' event received for {call_sid!r}")
                    break
                elif event_type == "mark":
                    logger.debug(f"{_LOG_PREFIX} Twilio mark event: {data.get('mark')}")
                else:
                    logger.debug(f"{_LOG_PREFIX} Unhandled Twilio event: {event_type!r}")

        except WebSocketDisconnect:
            logger.info(f"{_LOG_PREFIX} Twilio WebSocket disconnected for {call_sid!r}")
        except RuntimeError as exc:
            logger.info(f"{_LOG_PREFIX} WebSocket closed during receive (normal hangup race): {exc}")
        except Exception as exc:
            logger.error(f"{_LOG_PREFIX} Unexpected error in receive_from_twilio: {exc}", exc_info=True)

    tts_task = asyncio.create_task(bridge.tts_consumer(websocket), name=f"tts_consumer:{call_sid}")
    realtime_task = asyncio.create_task(realtime.receive_loop(), name=f"realtime_receive_loop:{call_sid}")

    async def _max_duration_watchdog() -> None:
        """Kill-switch: force-hangup if the call runs longer than MAX_CALL_DURATION_SECONDS."""
        await asyncio.sleep(MAX_CALL_DURATION_SECONDS)
        logger.warning(f"{_LOG_PREFIX} Max call duration reached for {call_sid!r} - forcing hangup")
        try:
            await realtime.inject_user_turn("[SYSTEM: max call duration reached - say a brief goodbye now]")
            await asyncio.sleep(10.0)
        except Exception:
            pass
        await _force_hangup_twilio(call_sid)
        try:
            await websocket.close()
        except Exception:
            pass

    watchdog_task = asyncio.create_task(_max_duration_watchdog(), name=f"max_duration_watchdog:{call_sid}")

    await realtime.trigger_initial_response()

    try:
        await receive_from_twilio()
    finally:
        logger.info(f"{_LOG_PREFIX} Call ended for {call_sid!r} - shutting down tasks")

        tts_task.cancel()
        realtime_task.cancel()
        watchdog_task.cancel()
        await asyncio.gather(tts_task, realtime_task, watchdog_task, return_exceptions=True)

        await realtime.disconnect()
        await tts.close()

        await _persist_call_outcome(session)
