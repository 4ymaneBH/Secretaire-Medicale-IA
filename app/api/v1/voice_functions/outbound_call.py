"""Outbound call handler.

Used for appointment confirmations, reminders, and satisfaction follow-ups.
On call connect, loads the related appointment (if any) and returns TwiML
<Connect><Stream> that opens a Twilio Media Stream to the OpenAI Realtime
API bridge (/ws/media-stream), which generates the greeting and manages
the full conversation.
"""
from typing import Optional
from datetime import datetime

from fastapi import Request, Form, Query
from fastapi.responses import PlainTextResponse, HTMLResponse
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream

from app.core.logging_config import get_logger
from app.core.config import settings

logger = get_logger(__name__)


async def handle_outbound_call(
    request: Request,
    call_sid: str = Form(...),
    job_id: Optional[str] = Query(None),
    purpose: str = Query("reminder"),
) -> PlainTextResponse:
    """Handle outbound call connect: create the CallSession and start the Realtime bridge."""
    try:
        language = settings.default_language
        appointment_id = job_id

        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from app.db.session import get_session_context
        from app.models.call_session import CallSession, CallDirection, CallStatus
        from app.models.appointment import Appointment

        async with get_session_context() as db:
            phone_number = "unknown"
            patient_id = None
            if appointment_id:
                result = await db.execute(
                    select(Appointment)
                    .options(selectinload(Appointment.patient))
                    .where(Appointment.id == int(appointment_id))
                )
                appointment = result.scalar_one_or_none()
                if appointment and appointment.patient:
                    language = appointment.patient.language_preference or language
                    phone_number = appointment.patient.phone_number
                    patient_id = appointment.patient_id

            existing_result = await db.execute(
                select(CallSession).where(CallSession.call_sid == call_sid)
            )
            call_session = existing_result.scalar_one_or_none()

            if not call_session:
                call_session = CallSession(
                    call_sid=call_sid,
                    phone_number=phone_number,
                    direction=CallDirection.OUTBOUND,
                    status=CallStatus.INITIATED,
                    language=language,
                    patient_id=patient_id,
                    appointment_id=int(appointment_id) if appointment_id else None,
                    started_at=datetime.utcnow(),
                )
                db.add(call_session)
            else:
                logger.info(f"Call session already exists for outbound call {call_sid}")

            await db.commit()

        base_url = settings.twilio_webhook_base_url.rstrip("/")
        ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_url}/api/v1/ws/media-stream"

        response = VoiceResponse()
        connect = Connect()
        stream = Stream(url=ws_url)
        stream.parameter(name="language", value=language)
        stream.parameter(name="purpose", value=purpose)
        if appointment_id:
            stream.parameter(name="appointment_id", value=str(appointment_id))
        connect.append(stream)
        response.append(connect)

        logger.info(f"Returning Connect/Stream TwiML for outbound call {call_sid} -> {ws_url}")
        return HTMLResponse(content=str(response), media_type="application/xml")

    except Exception as e:
        logger.error(f"Unhandled error for outbound call {call_sid}: {e}", exc_info=True)
        error_twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response><Say>Erreur technique. Au revoir.</Say><Hangup/></Response>"
        )
        return PlainTextResponse(content=error_twiml, media_type="text/xml")
