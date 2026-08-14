"""Voice endpoint for telephony provider call-status webhooks."""
from datetime import datetime
from fastapi import Request

from app.core.logging_config import get_logger

logger = get_logger(__name__)

ENDED_STATUSES = {"completed", "busy", "no-answer", "failed", "canceled"}

async def handle_call_status(request: Request):
    """Handle Twilio call status callbacks.

    Statuses: initiated, ringing, in-progress, completed, busy, no-answer, failed, canceled
    """
    try:
        from sqlalchemy import select
        from app.db.session import get_session_context
        from app.models.call_session import CallSession, CallStatus as CallStatusEnum

        form = await request.form()
        call_sid = form.get("CallSid", "unknown")
        call_status = form.get("CallStatus", "unknown")
        call_duration = form.get("CallDuration", "0")
        recording_url = form.get("RecordingUrl")

        if recording_url and recording_url.lower() in ("none", "null", ""):
            recording_url = None

        logger.info(f"[CALL STATUS] CallSid: {call_sid}, Status: {call_status}, Duration: {call_duration}s")

        status_map = {
            "initiated": CallStatusEnum.INITIATED,
            "ringing": CallStatusEnum.RINGING,
            "in-progress": CallStatusEnum.IN_PROGRESS,
            "completed": CallStatusEnum.COMPLETED,
            "busy": CallStatusEnum.BUSY,
            "no-answer": CallStatusEnum.NO_ANSWER,
            "failed": CallStatusEnum.FAILED,
            "canceled": CallStatusEnum.CANCELED,
        }
        db_status = status_map.get(call_status.lower(), CallStatusEnum.FAILED)

        async with get_session_context() as db:
            result = await db.execute(
                select(CallSession).where(CallSession.call_sid == call_sid)
            )
            call_session = result.scalar_one_or_none()

            if not call_session:
                logger.warning(f"[CALL STATUS] No call session found for CallSid {call_sid}")
                return {"received": True, "call_sid": call_sid, "status": call_status}

            call_session.status = db_status
            call_session.duration_seconds = int(call_duration)
            if recording_url:
                call_session.recording_url = recording_url
            if call_status.lower() in ENDED_STATUSES:
                call_session.ended_at = datetime.utcnow()

            await db.commit()
            logger.info("[CALL STATUS] Call session updated")

        return {"received": True, "call_sid": call_sid, "status": call_status}

    except Exception as e:
        logger.error(f"[CALL STATUS] Error handling call status: {e}", exc_info=True)
        return {"received": True, "error": str(e)}
