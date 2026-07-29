"""Voice endpoints for telephony provider webhooks (inbound, outbound, status)."""
from fastapi import APIRouter, Request, WebSocket, Form, Query
from fastapi.responses import PlainTextResponse

from app.core.logging_config import get_logger

from typing import Optional

router = APIRouter()
logger = get_logger(__name__)


@router.post("/voice/inbound", response_class=PlainTextResponse)
async def voice_inbound_endpoint(request: Request):
    """Handle inbound patient calls and start the AI secretary conversation."""
    from app.api.v1.voice_functions.inbound_call import handle_inbound_call
    return await handle_inbound_call(request)


@router.post("/voice/outbound", response_class=PlainTextResponse)
async def voice_outbound_endpoint(
    request: Request,
    call_sid: str = Form(..., alias="CallSid"),
    job_id: Optional[str] = Query(None)
):
    """Delegate to handle_outbound_call implementation in voice_functions."""
    from app.api.v1.voice_functions.outbound_call import handle_outbound_call
    return await handle_outbound_call(request=request, call_sid=call_sid, job_id=job_id)


@router.post("/voice/status")
async def voice_status_endpoint(request: Request):
    """Delegate to voice_status implementation in voice_functions."""
    from app.api.v1.voice_functions.voice_status import handle_call_status
    return await handle_call_status(request)


@router.websocket("/ws/media-stream")
async def media_stream_endpoint(websocket: WebSocket):
    """Delegate to media_stream_handler - Twilio Media Stream <-> OpenAI Realtime bridge."""
    from app.api.v1.voice_functions.media_stream import media_stream_handler
    await media_stream_handler(websocket)
