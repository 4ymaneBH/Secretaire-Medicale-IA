"""Voice endpoints for telephony provider webhooks (inbound, outbound, status)."""
from fastapi import APIRouter, Request, WebSocket, Form, Query, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from app.core.logging_config import get_logger
from app.api.deps import verify_internal_api_key, verify_twilio_signature, rate_limit
from app.services.voice.outbound_dispatcher import OUTBOUND_PURPOSES

from typing import Optional

router = APIRouter()
logger = get_logger(__name__)


async def rate_limit_twilio_voice(request: Request) -> None:
    """Apply the dedicated rate limit used by Twilio voice webhooks."""
    await rate_limit(request, key_prefix="rl:twilio:voice", limit=60, window_seconds=60)


async def rate_limit_twilio_status(request: Request) -> None:
    """Apply the dedicated rate limit used by Twilio status callbacks."""
    await rate_limit(request, key_prefix="rl:twilio:status", limit=120, window_seconds=60)


async def rate_limit_internal_trigger(request: Request) -> None:
    """Apply the rate limit used by the internal outbound-call trigger.

    Wrapping is required: depending on `rate_limit` directly would expose its
    `limit`/`window_seconds` arguments as caller-supplied query parameters.
    """
    await rate_limit(request, key_prefix="rl:internal:trigger", limit=30, window_seconds=60)


@router.post(
    "/voice/inbound",
    response_class=PlainTextResponse,
    dependencies=[Depends(verify_twilio_signature), Depends(rate_limit_twilio_voice)],
)
async def voice_inbound_endpoint(request: Request):
    """Handle inbound patient calls and start the AI secretary conversation."""
    from app.api.v1.voice_functions.inbound_call import handle_inbound_call
    return await handle_inbound_call(request)


@router.post(
    "/voice/outbound",
    response_class=PlainTextResponse,
    dependencies=[Depends(verify_twilio_signature), Depends(rate_limit_twilio_voice)],
)
async def voice_outbound_endpoint(
    request: Request,
    call_sid: str = Form(..., alias="CallSid"),
    job_id: Optional[str] = Query(None),
    purpose: str = Query("reminder"),
):
    """Delegate to handle_outbound_call implementation in voice_functions."""
    from app.api.v1.voice_functions.outbound_call import handle_outbound_call
    return await handle_outbound_call(request=request, call_sid=call_sid, job_id=job_id, purpose=purpose)


@router.post(
    "/voice/outbound/trigger/{appointment_id}",
    dependencies=[Depends(verify_internal_api_key), Depends(rate_limit_internal_trigger)],
    summary="Trigger an outbound call for an appointment",
    responses={
        200: {
            "description": "Call created by the telephony provider",
            "content": {
                "application/json": {
                    "example": {"success": True, "call_sid": "CAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}
                }
            },
        },
        400: {"description": "`purpose` is not one of the five supported call types"},
        401: {"description": "Missing or invalid X-Internal-API-Key header"},
        404: {"description": "No appointment exists with this id"},
        409: {"description": "The appointment has no patient with a phone number, so it cannot be called"},
        502: {"description": "The telephony provider failed to create the call"},
    },
)
async def trigger_outbound_call_endpoint(
    appointment_id: int,
    purpose: str = Query(
        "reminder",
        description="Which automated call script to run.",
        json_schema_extra={"enum": sorted(OUTBOUND_PURPOSES)},
    ),
):
    """Place an outbound call for an appointment (confirmation, reminder, satisfaction survey,
    exam reminder, or administrative follow-up).

    Intended to be called by a scheduler or by Module 1 (central backend) — not by patients.
    """
    from app.services.voice.outbound_dispatcher import (
        AppointmentNotFoundError,
        InvalidPurposeError,
        PatientNotCallableError,
        TelephonyProviderError,
        initiate_call_for_appointment,
    )

    try:
        call_sid = await initiate_call_for_appointment(appointment_id, purpose)
    except InvalidPurposeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except AppointmentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PatientNotCallableError as exc:
        # 409, not 422: FastAPI already returns 422 for its own request-validation
        # failures (a non-integer appointment_id), and Module 1 needs to tell the
        # two apart.
        raise HTTPException(status_code=409, detail=str(exc))
    except TelephonyProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {"success": True, "call_sid": call_sid}


@router.post(
    "/voice/status",
    dependencies=[Depends(verify_twilio_signature), Depends(rate_limit_twilio_status)],
)
async def voice_status_endpoint(request: Request):
    """Delegate to voice_status implementation in voice_functions."""
    from app.api.v1.voice_functions.voice_status import handle_call_status
    return await handle_call_status(request)


@router.websocket("/ws/media-stream")
async def media_stream_endpoint(websocket: WebSocket):
    """Delegate to media_stream_handler - Twilio Media Stream <-> OpenAI Realtime bridge."""
    from app.api.v1.voice_functions.media_stream import media_stream_handler
    await media_stream_handler(websocket)
