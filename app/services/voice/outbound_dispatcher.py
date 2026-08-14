"""Initiates outbound calls for appointments (confirmation, reminder, satisfaction survey, etc.)."""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.db.session import get_session_context
from app.models.appointment import Appointment
from app.services.integrations.telephony.provider import get_telephony_provider

settings = get_settings()
logger = get_logger(__name__)

# The five automatic outbound call types named in the CDC.
OUTBOUND_PURPOSES = {
    "confirmation",
    "reminder",
    "satisfaction_survey",
    "exam_reminder",
    "admin_followup",
}


async def initiate_call_for_appointment(appointment_id: int, purpose: str = "reminder") -> Optional[str]:
    """Place an outbound call for an appointment. Returns the Twilio call SID, or None on failure."""
    if purpose not in OUTBOUND_PURPOSES:
        logger.warning(f"Unknown outbound call purpose {purpose!r}, defaulting to 'reminder'")
        purpose = "reminder"

    async with get_session_context() as db:
        result = await db.execute(
            select(Appointment)
            .options(selectinload(Appointment.patient))
            .where(Appointment.id == appointment_id)
        )
        appointment = result.scalar_one_or_none()

    if not appointment or not appointment.patient:
        logger.error(f"No appointment/patient found for appointment_id={appointment_id}")
        return None

    base_url = settings.twilio_webhook_base_url.rstrip("/")
    callback_url = f"{base_url}/api/v1/voice/outbound?job_id={appointment_id}&purpose={purpose}"
    status_callback_url = f"{base_url}/api/v1/voice/status"

    provider = get_telephony_provider()
    call_sid = await provider.initiate_outbound_call(
        to_number=appointment.patient.phone_number,
        callback_url=callback_url,
        status_callback_url=status_callback_url,
    )

    if call_sid:
        logger.info(f"Outbound call initiated: sid={call_sid} appointment={appointment_id} purpose={purpose}")
    else:
        logger.error(f"Failed to initiate outbound call for appointment={appointment_id}")

    return call_sid
