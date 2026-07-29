"""Inbound call handler.

On call connect, creates a CallSession record and returns TwiML
<Connect><Stream> that opens a Twilio Media Stream to the OpenAI Realtime
API bridge (/ws/media-stream), which handles the greeting and the full
conversation.
"""
from datetime import datetime
from typing import Any

from fastapi import Request, Depends
from fastapi.responses import HTMLResponse, PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream

from app.core.logging_config import get_logger
from app.core.config import settings
from app.api.deps import verify_twilio_signature, rate_limit

logger = get_logger(__name__)


async def rate_limit_twilio_voice(request: Request) -> None:
    """Rate limit for Twilio voice webhooks."""
    await rate_limit(request, key_prefix="rl:twilio", limit=60, window_seconds=60)


async def handle_inbound_call(
    request: Request,
    _sig: Any = Depends(verify_twilio_signature),
    _rl: Any = Depends(rate_limit_twilio_voice),
):
    """Handle an inbound patient call and start the AI secretary conversation."""
    try:
        form = await request.form()
        call_sid = form.get("CallSid", "unknown")
        from_number = form.get("From", "unknown")
        logger.info(f"Inbound call from {from_number}, CallSid: {call_sid}")

        from sqlalchemy import select
        from app.db.session import get_session_context
        from app.models.call_session import CallSession, CallDirection, CallStatus

        async with get_session_context() as db:
            result = await db.execute(
                select(CallSession).where(CallSession.call_sid == call_sid)
            )
            call_session = result.scalar_one_or_none()

            if not call_session:
                call_session = CallSession(
                    call_sid=call_sid,
                    phone_number=from_number,
                    direction=CallDirection.INBOUND,
                    status=CallStatus.INITIATED,
                    language=settings.default_language,
                    started_at=datetime.utcnow(),
                )
                db.add(call_session)
                await db.commit()
                await db.refresh(call_session)
                logger.info(f"Call session created: id={call_session.id}, CallSid={call_sid}")
            else:
                logger.info(f"Call session already exists: id={call_session.id}")

        base_url = settings.twilio_webhook_base_url.rstrip("/")
        ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_url}/api/v1/ws/media-stream"

        response = VoiceResponse()
        connect = Connect()
        stream = Stream(url=ws_url)
        stream.parameter(name="language", value=settings.default_language)
        connect.append(stream)
        response.append(connect)

        logger.info(f"Returning Connect/Stream TwiML for {call_sid} -> {ws_url}")
        return HTMLResponse(content=str(response), media_type="application/xml")

    except Exception as e:
        logger.error(f"Error handling inbound call: {e}", exc_info=True)
        error_twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response><Say>Erreur technique. Au revoir.</Say><Hangup/></Response>"
        )
        return PlainTextResponse(content=error_twiml, media_type="text/xml")
