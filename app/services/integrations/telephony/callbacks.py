"""Telephony status callbacks handler."""
from typing import Dict, Any
from app.core.logging_config import get_logger

logger = get_logger(__name__)


async def handle_call_status_callback(callback_data: Dict[str, Any]) -> None:
    """
    Handle telephony provider status callbacks.
    
    Updates call session status based on provider callbacks.
    """
    call_sid = callback_data.get("CallSid") or callback_data.get("call_uuid")
    call_status = callback_data.get("CallStatus") or callback_data.get("call_status")
    
    logger.info(f"Call status callback: {call_sid} -> {call_status}")
    
    # TODO: Update call_session in database
    # from app.repositories.call_repo import update_call_status
    # await update_call_status(call_sid, call_status)
    
    # Map provider-specific status to internal status
    status_map = {
        "initiated": "initiated",
        "ringing": "ringing",
        "in-progress": "in_progress",
        "answered": "in_progress",
        "completed": "completed",
        "busy": "busy",
        "no-answer": "no_answer",
        "failed": "failed",
        "canceled": "canceled",
    }
    
    internal_status = status_map.get(call_status.lower(), call_status)
    logger.info(f"Mapped to internal status: {internal_status}")
