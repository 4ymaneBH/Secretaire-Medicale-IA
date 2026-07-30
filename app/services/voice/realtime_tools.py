"""Realtime tool execution for OpenAI Realtime API function calls.

When the OpenAI Realtime API fires a function-call event, the media-stream
handler calls :func:`execute_tool`. This module dispatches the call, mutates
the in-memory :class:`CallSessionState` for the current call, persists
appointment confirmations, and returns a JSON string forwarded back to
OpenAI as the tool output.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from app.core.logging_config import get_logger

logger = get_logger(__name__)

_LOG_PREFIX = "[RealtimeTools]"

SUPPORTED_LANGUAGES = {"fr", "en", "ar", "it", "es"}
BUSINESS_HOURS = range(9, 17)  # 09:00-17:00


@dataclass
class CallSessionState:
    """In-memory state for a single call, mutated by tool calls as the conversation progresses."""

    call_sid: str
    phone_number: Optional[str] = None
    language: str = "fr"
    patient_id: Optional[int] = None
    proposed_slot: Optional[str] = None
    appointment_confirmed: bool = False
    appointment_id: Optional[int] = None
    transfer_requested: bool = False
    transfer_reason: Optional[str] = None
    urgency_detected: bool = False
    summary: Optional[str] = None
    phase: str = "in_progress"


async def execute_tool(
    call_id: str,
    name: str,
    args_json: str,
    session: CallSessionState,
) -> str:
    """Dispatch a function-call event from the OpenAI Realtime API.

    Returns a JSON string to forward to OpenAI as the function-call output.
    The ``"status"`` sentinel signals the media-stream handler what to do next:
    ``"hangup"`` ends the call, ``"transfer"`` redirects it to a human.
    """
    try:
        try:
            args: dict[str, Any] = json.loads(args_json) if args_json else {}
        except json.JSONDecodeError as exc:
            logger.warning(f"{_LOG_PREFIX} Malformed args_json for tool {name!r}: {exc}")
            args = {}

        logger.info(f"{_LOG_PREFIX} Executing tool={name!r} call_id={call_id!r}")

        if name == "get_available_slots":
            return await _handle_get_available_slots(args)
        if name == "propose_appointment_slot":
            return await _handle_propose_slot(args, session)
        if name == "confirm_appointment":
            return await _handle_confirm_appointment(args, session)
        if name == "transfer_to_human":
            return await _handle_transfer_to_human(args, session)
        if name == "switch_language":
            return await _handle_switch_language(args, session)
        if name == "recall_context":
            return await _handle_recall_context(args, session)
        if name == "end_call":
            return await _handle_end_call(args, session)

        logger.warning(f"{_LOG_PREFIX} Unknown tool requested: {name!r}")
        return json.dumps({"status": "error", "message": f"Unknown tool: {name}"})

    except Exception as exc:
        logger.error(f"{_LOG_PREFIX} Error executing tool {name!r}: {exc}", exc_info=True)
        return json.dumps({"status": "error", "message": str(exc)})


async def _handle_get_available_slots(args: dict[str, Any]) -> str:
    """Return real available appointment slots (business hours, excluding already-booked ones).

    Args schema:
        days_ahead (int) — optional, how many days ahead to search (default 7)
    """
    from sqlalchemy import select
    from app.db.session import get_session_context
    from app.models.appointment import Appointment, AppointmentStatus

    days_ahead = int(args.get("days_ahead", 7))
    now = datetime.utcnow()

    candidate_slots = []
    for day_offset in range(1, days_ahead + 1):
        day = now + timedelta(days=day_offset)
        if day.weekday() >= 5:  # skip weekends
            continue
        for hour in BUSINESS_HOURS:
            candidate_slots.append(day.replace(hour=hour, minute=0, second=0, microsecond=0))

    async with get_session_context() as db:
        result = await db.execute(
            select(Appointment.scheduled_at).where(
                Appointment.status != AppointmentStatus.CANCELED,
                Appointment.scheduled_at >= now,
            )
        )
        booked = {row[0].replace(tzinfo=None) for row in result.all()}

    available = [s for s in candidate_slots if s not in booked][:5]
    return json.dumps({"status": "ok", "slots": [s.isoformat() for s in available]})


async def _handle_propose_slot(args: dict[str, Any], session: CallSessionState) -> str:
    """Propose an appointment slot to the patient.

    Args schema:
        slot (str) — required, ISO 8601 datetime of the proposed slot
    """
    slot = args.get("slot")
    if not slot:
        return json.dumps({"status": "error", "message": "slot is required"})

    try:
        datetime.fromisoformat(slot)
    except ValueError:
        return json.dumps({"status": "error", "message": "slot must be an ISO 8601 datetime"})

    session.proposed_slot = slot
    logger.info(f"{_LOG_PREFIX} Proposed slot {slot!r}")
    return json.dumps({"status": "ok", "message": f"Slot {slot} proposed"})


async def _handle_confirm_appointment(args: dict[str, Any], session: CallSessionState) -> str:
    """Confirm the previously proposed appointment slot and persist it.

    Args schema:
        reason (str) — optional, patient's stated reason for the visit
    """
    if not session.proposed_slot:
        return json.dumps({"status": "error", "message": "No slot has been proposed yet"})

    from app.db.session import get_session_context
    from app.models.appointment import Appointment, AppointmentStatus

    reason = args.get("reason")

    async with get_session_context() as db:
        appointment = Appointment(
            patient_id=session.patient_id,
            scheduled_at=datetime.fromisoformat(session.proposed_slot),
            reason=reason,
            status=AppointmentStatus.CONFIRMED,
        )
        db.add(appointment)
        await db.commit()
        await db.refresh(appointment)

    session.appointment_confirmed = True
    session.appointment_id = appointment.id
    if reason:
        session.summary = reason

    logger.info(f"{_LOG_PREFIX} Appointment {appointment.id} confirmed for slot {session.proposed_slot!r}")
    return json.dumps({"status": "ok", "message": "Appointment confirmed", "appointment_id": appointment.id})


async def _handle_transfer_to_human(args: dict[str, Any], session: CallSessionState) -> str:
    """Transfer this call to a human secretary/practitioner.

    Args schema:
        reason  (str) — required
        urgent  (bool) — whether this is an emergency requiring immediate transfer
    """
    reason: str = args.get("reason", "unspecified reason")
    urgent: bool = bool(args.get("urgent", False))

    session.transfer_requested = True
    session.transfer_reason = reason
    if urgent:
        session.urgency_detected = True

    logger.warning(f"{_LOG_PREFIX} transfer_to_human triggered (urgent={urgent}, reason={reason!r})")
    return json.dumps({"status": "transfer", "message": "Transferring to a human", "urgent": urgent})


async def _handle_switch_language(args: dict[str, Any], session: CallSessionState) -> str:
    """Switch the conversation language mid-call.

    Args schema:
        language (str) — required, one of fr/en/ar/it/es
    """
    language = args.get("language", "").lower()
    if language not in SUPPORTED_LANGUAGES:
        return json.dumps({"status": "error", "message": f"Unsupported language: {language}"})

    session.language = language
    logger.info(f"{_LOG_PREFIX} Language switched to {language!r}")
    return json.dumps({"status": "language_switched", "language": language})


async def _handle_recall_context(args: dict[str, Any], session: CallSessionState) -> str:
    """Recall relevant context from this patient's previous calls.

    Args schema:
        query (str) — required, what the patient just referenced (e.g. "my last visit")
    """
    query = args.get("query")
    if not query:
        return json.dumps({"status": "error", "message": "query is required"})
    if not session.patient_id:
        return json.dumps({"status": "ok", "context": None})

    from app.services.ai.memory_service import get_memory_service

    context = await get_memory_service().recall_context(session.patient_id, query)
    return json.dumps({"status": "ok", "context": context})


async def _handle_end_call(args: dict[str, Any], session: CallSessionState) -> str:
    """Signal that the call should end.

    Args schema:
        reason (str) — optional
    """
    reason: str = args.get("reason", "call completed")

    if not session.summary:
        session.summary = f"Appel terminé ({reason})."

    session.phase = "hangup"

    logger.info(f"{_LOG_PREFIX} end_call requested (reason={reason!r})")
    return json.dumps({"status": "hangup", "message": "Call ending", "reason": reason})
