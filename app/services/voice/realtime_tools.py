"""Realtime tool execution for OpenAI Realtime API function calls.

When the OpenAI Realtime API fires a function-call event, the media-stream
handler calls :func:`execute_tool`. This module dispatches the call, mutates
the in-memory :class:`CallSessionState` for the current call, and returns a
JSON string forwarded back to OpenAI as the tool output.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.logging_config import get_logger

logger = get_logger(__name__)

_LOG_PREFIX = "[RealtimeTools]"


@dataclass
class CallSessionState:
    """In-memory state for a single call, mutated by tool calls as the conversation progresses."""

    call_sid: str
    phone_number: Optional[str] = None
    language: str = "fr"
    proposed_slot: Optional[str] = None
    appointment_confirmed: bool = False
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
    The ``"status": "hangup"`` sentinel signals the media-stream handler to
    trigger a Twilio hangup.
    """
    try:
        try:
            args: dict[str, Any] = json.loads(args_json) if args_json else {}
        except json.JSONDecodeError as exc:
            logger.warning(f"{_LOG_PREFIX} Malformed args_json for tool {name!r}: {exc}")
            args = {}

        logger.info(f"{_LOG_PREFIX} Executing tool={name!r} call_id={call_id!r}")

        if name == "propose_appointment_slot":
            return await _handle_propose_slot(args, session)
        if name == "confirm_appointment":
            return await _handle_confirm_appointment(args, session)
        if name == "transfer_to_human":
            return await _handle_transfer_to_human(args, session)
        if name == "end_call":
            return await _handle_end_call(args, session)

        logger.warning(f"{_LOG_PREFIX} Unknown tool requested: {name!r}")
        return json.dumps({"status": "error", "message": f"Unknown tool: {name}"})

    except Exception as exc:
        logger.error(f"{_LOG_PREFIX} Error executing tool {name!r}: {exc}", exc_info=True)
        return json.dumps({"status": "error", "message": str(exc)})


async def _handle_propose_slot(args: dict[str, Any], session: CallSessionState) -> str:
    """Propose an appointment slot to the patient.

    Args schema:
        slot (str) — required, ISO datetime of the proposed slot
    """
    slot = args.get("slot")
    if not slot:
        return json.dumps({"status": "error", "message": "slot is required"})

    session.proposed_slot = slot
    logger.info(f"{_LOG_PREFIX} Proposed slot {slot!r}")
    return json.dumps({"status": "ok", "message": f"Slot {slot} proposed"})


async def _handle_confirm_appointment(args: dict[str, Any], session: CallSessionState) -> str:
    """Confirm the previously proposed appointment slot.

    Args schema:
        reason (str) — optional, patient's stated reason for the visit
    """
    if not session.proposed_slot:
        return json.dumps({"status": "error", "message": "No slot has been proposed yet"})

    session.appointment_confirmed = True
    reason = args.get("reason")
    if reason:
        session.summary = reason

    logger.info(f"{_LOG_PREFIX} Appointment confirmed for slot {session.proposed_slot!r}")
    return json.dumps({"status": "ok", "message": "Appointment confirmed"})


async def _handle_transfer_to_human(args: dict[str, Any], session: CallSessionState) -> str:
    """Flag this call for transfer to a human secretary/practitioner.

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
    return json.dumps({"status": "ok", "message": "Transfer flagged"})


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
