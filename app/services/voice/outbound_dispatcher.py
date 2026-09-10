"""Initiates outbound calls for appointments (confirmation, reminder, satisfaction survey, etc.)."""
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


class OutboundCallError(Exception):
    """Base class for outbound dispatch failures.

    Callers (the API layer, Module 1) need to tell these apart: a missing
    appointment is the caller's mistake, a provider failure is ours.
    """


class InvalidPurposeError(OutboundCallError):
    """`purpose` is not one of the five call types defined in the CDC."""


class AppointmentNotFoundError(OutboundCallError):
    """No appointment exists with this id."""


class PatientNotCallableError(OutboundCallError):
    """The appointment exists but has no patient, or the patient has no phone number."""


class TelephonyProviderError(OutboundCallError):
    """The telephony provider refused or failed to create the call."""


async def initiate_call_for_appointment(appointment_id: int, purpose: str = "reminder") -> str:
    """Place an outbound call for an appointment and return the Twilio call SID.

    Raises an :class:`OutboundCallError` subclass on failure — never places a call
    with a substituted purpose, since that would read the wrong script to a patient.
    """
    if purpose not in OUTBOUND_PURPOSES:
        raise InvalidPurposeError(
            f"Unknown outbound call purpose {purpose!r}. "
            f"Expected one of: {', '.join(sorted(OUTBOUND_PURPOSES))}"
        )

    async with get_session_context() as db:
        result = await db.execute(
            select(Appointment)
            .options(selectinload(Appointment.patient))
            .where(Appointment.id == appointment_id)
        )
        appointment = result.scalar_one_or_none()

    if not appointment:
        logger.error(f"No appointment found for appointment_id={appointment_id}")
        raise AppointmentNotFoundError(f"No appointment with id={appointment_id}")

    if not appointment.patient or not (appointment.patient.phone_number or "").strip():
        logger.error(f"Appointment {appointment_id} has no reachable patient phone number")
        raise PatientNotCallableError(
            f"Appointment {appointment_id} has no patient with a phone number"
        )

    base_url = settings.twilio_webhook_base_url.rstrip("/")
    callback_url = f"{base_url}/api/v1/voice/outbound?job_id={appointment_id}&purpose={purpose}"
    status_callback_url = f"{base_url}/api/v1/voice/status"

    provider = get_telephony_provider()
    call_sid = await provider.initiate_outbound_call(
        to_number=appointment.patient.phone_number,
        callback_url=callback_url,
        status_callback_url=status_callback_url,
    )

    if not call_sid:
        logger.error(f"Failed to initiate outbound call for appointment={appointment_id}")
        raise TelephonyProviderError(
            f"Telephony provider did not return a call SID for appointment={appointment_id}"
        )

    logger.info(f"Outbound call initiated: sid={call_sid} appointment={appointment_id} purpose={purpose}")
    return call_sid
