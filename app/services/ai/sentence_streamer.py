"""
Sentence streaming: drains LLM token stream, detects sentence boundaries,
writes queued sentences to Redis for chained Twilio TwiML delivery.
"""
import asyncio
import json
from typing import AsyncGenerator, Optional

from app.api.deps import get_redis_client
from app.core.logging_config import get_logger

logger = get_logger(__name__)

_STREAM_PREFIX = "sent_stream:"
_STREAM_TTL = 60  # seconds — long enough for entire call turn


def _is_boundary(buffer: str) -> bool:
    """Return True if buffer ends at a natural sentence boundary."""
    stripped = buffer.rstrip()
    if len(stripped) < 10:
        return False
    if stripped[-1] in ".!?":
        return True
    if len(stripped) > 200 and stripped[-1] in ",;":
        return True
    return False


def _write_redis(
    redis,
    call_sid: str,
    stream_id: str,
    sentences: list,
    is_complete: bool,
    lang: str,
    intent: str,
) -> None:
    """Write current stream state to Redis."""
    if not redis:
        return
    try:
        key = f"{_STREAM_PREFIX}{call_sid}:{stream_id}"
        redis.setex(
            key,
            _STREAM_TTL,
            json.dumps({
                "sentences": sentences,
                "is_complete": is_complete,
                "lang": lang,
                "intent": intent,
            }),
        )
    except Exception as e:
        logger.warning(f"[SentStream] Redis write failed for {call_sid}: {e}")


async def stream_to_redis(
    token_stream: AsyncGenerator[str, None],
    call_sid: str,
    stream_id: str,
    lang: str,
    intent: str,
) -> None:
    """
    Background coroutine: consume the LLM token stream, detect sentence
    boundaries, and write each completed sentence to Redis immediately.

    Caller launches this with asyncio.create_task() and does NOT await it.
    """
    redis = get_redis_client()
    buffer = ""
    sentences = []

    try:
        async for token in token_stream:
            buffer += token
            if _is_boundary(buffer):
                sentence = buffer.strip()
                if sentence:
                    sentences.append(sentence)
                    _write_redis(
                        redis, call_sid, stream_id, sentences,
                        is_complete=False, lang=lang, intent=intent,
                    )
                buffer = ""

        # Flush any remaining text not ending with punctuation
        if buffer.strip():
            sentences.append(buffer.strip())

        _write_redis(
            redis, call_sid, stream_id, sentences,
            is_complete=True, lang=lang, intent=intent,
        )
        logger.debug(
            f"[SentStream] {call_sid}/{stream_id}: complete — {len(sentences)} sentences"
        )

    except Exception as e:
        logger.error(f"[SentStream] {call_sid}: stream error: {e}")
        _write_redis(
            redis, call_sid, stream_id, sentences,
            is_complete=True, lang=lang, intent=intent,
        )


async def wait_for_sentence(
    call_sid: str,
    stream_id: str,
    index: int,
    timeout: float = 3.0,
) -> Optional[dict]:
    """
    Poll Redis until sentences[index] is available or timeout is reached.

    Returns the full state dict (sentences, is_complete, lang, intent),
    or None on timeout / Redis unavailable.
    """
    redis = get_redis_client()
    if not redis:
        return None

    key = f"{_STREAM_PREFIX}{call_sid}:{stream_id}"
    deadline = asyncio.get_event_loop().time() + timeout

    while asyncio.get_event_loop().time() < deadline:
        try:
            raw = redis.get(key)
            if raw:
                state = json.loads(raw)
                sentences = state.get("sentences", [])
                is_complete = state.get("is_complete", False)
                # Return as soon as requested index is available OR stream is done
                if index < len(sentences) or is_complete:
                    return state
        except Exception as e:
            logger.warning(f"[SentStream] Redis read error for {call_sid}: {e}")

        await asyncio.sleep(0.05)  # 50ms poll interval

    logger.warning(
        f"[SentStream] Timeout waiting for sentence {index} of {call_sid}/{stream_id}"
    )
    return None


async def get_assembled_text(
    call_sid: str,
    stream_id: str,
    timeout: float = 10.0,
) -> str:
    """
    Wait for stream to complete and return full response text assembled from sentences.
    Used by background DB-save tasks to persist the full transcript.
    """
    redis = get_redis_client()
    if not redis:
        return ""

    key = f"{_STREAM_PREFIX}{call_sid}:{stream_id}"
    deadline = asyncio.get_event_loop().time() + timeout

    while asyncio.get_event_loop().time() < deadline:
        try:
            raw = redis.get(key)
            if raw:
                state = json.loads(raw)
                if state.get("is_complete"):
                    return " ".join(state.get("sentences", []))
        except Exception:
            pass
        await asyncio.sleep(0.1)

    return ""
