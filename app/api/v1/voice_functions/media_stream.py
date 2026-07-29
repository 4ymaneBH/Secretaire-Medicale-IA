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
from datetime import datetime

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

router = APIRouter()


async def _force_hangup_twilio(call_sid: str) -> bool:
    """Terminate a Twilio PSTN call via REST API.

    Closing the WebSocket alone does not hang up the phone — Twilio keeps
    the call alive and continues billing until the call is explicitly
    marked completed via the REST API.
    """
    from twilio.rest import Client as TwilioClient
    from app.core.config import get_settings

    settings = get_settings()
    client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)

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

            outcome_type = (
                OutcomeType.TRANSFERRED if session.transfer_requested
                else OutcomeType.RESOLVED
            )
            outcome = Outcome(
                call_session_id=call_session.id,
                outcome_type=outcome_type,
                outcome_details=session.summary,
                issue_resolved="yes" if session.appointment_confirmed else "unknown",
                transfer_reason=TransferReason.URGENT_KEYWORD if session.urgency_detected else (
                    TransferReason.EXPLICIT_REQUEST if session.transfer_requested else None
                ),
            )
            db.add(outcome)
            await db.commit()
            logger.info(f"{_LOG_PREFIX} Outcome persisted for {session.call_sid!r}")
    except Exception as exc:
        logger.error(f"{_LOG_PREFIX} Failed to persist call outcome: {exc}", exc_info=True)


@router.websocket("/ws/media-stream")
async def media_stream_handler(websocket: WebSocket) -> None:
    """Handle a Twilio Media Stream WebSocket for one call (inbound or outbound)."""

    await websocket.accept()
    logger.info(f"{_LOG_PREFIX} WebSocket accepted - waiting for Twilio start event")

    stream_sid: str = ""
    call_sid: str = ""
    language: str = "fr"
    appointment_id: str | None = None

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

    session = CallSessionState(call_sid=call_sid, language=language)

    system_prompt = build_system_prompt(language, appointment_id=appointment_id)
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

    realtime.on_text_delta = bridge.on_text_delta
    realtime.on_text_done = bridge.on_text_done

    async def on_speech_started() -> None:
        """Barge-in: user started speaking - interrupt in-flight TTS output."""
        logger.info(f"{_LOG_PREFIX} Barge-in detected for {call_sid!r}")
        bridge.reset_for_new_turn()
        await bridge.interrupt(websocket)

    realtime.on_speech_started = on_speech_started

    async def on_tool_call(tool_call_id: str, name: str, args_json: str) -> None:
        logger.info(f"{_LOG_PREFIX} Tool call received: name={name!r}")

        result_json = await execute_tool(tool_call_id, name, args_json, session)

        is_terminal = name == "end_call"
        await realtime.send_tool_result(tool_call_id, result_json, trigger_response=not is_terminal)

        if is_terminal:
            try:
                result = json.loads(result_json)
                if result.get("status") == "hangup":
                    logger.info(f"{_LOG_PREFIX} Hangup sentinel received - waiting for TTS to finish")
                    await bridge.wait_until_drained(timeout=15.0)
                    await asyncio.sleep(1.0)
                    await _force_hangup_twilio(call_sid)
                    try:
                        await websocket.close()
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning(f"{_LOG_PREFIX} Could not inspect tool result for hangup: {exc}")

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
            }.get(language, "[SYSTEM: Audio was unclear. Politely ask the patient to repeat.]")
            await realtime.inject_user_turn(clarify)
            return

        try:
            from app.db.session import get_session_context
            from sqlalchemy import select
            from app.models.call_session import CallSession
            from app.models.transcript import Transcript

            async with get_session_context() as db:
                result = await db.execute(
                    select(CallSession).where(CallSession.call_sid == call_sid)
                )
                call_session = result.scalar_one_or_none()
                if call_session:
                    db.add(Transcript(
                        call_session_id=call_session.id,
                        speaker="user",
                        text=cleaned,
                        language=language,
                        timestamp=datetime.utcnow(),
                    ))
                    await db.commit()
        except Exception as exc:
            logger.error(f"{_LOG_PREFIX} Failed to persist transcript: {exc}", exc_info=True)

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
