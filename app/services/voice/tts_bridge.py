"""TTSBridge: streams OpenAI Realtime text deltas → ElevenLabs TTS → Twilio Media Stream.

Flow:
    OpenAI text delta
        → buffer accumulation
        → sentence boundary detection
        → asyncio.Queue[str | None]
        → ElevenLabs stream_synthesize(sentence)
        → MP3 chunks base64-encoded
        → Twilio "media" WebSocket event

The bridge is designed to run for the full lifetime of a call.  The
``tts_consumer`` coroutine is launched once as an ``asyncio.Task`` and
processes multiple conversation turns, using ``None`` sentinels to mark
the end of each turn rather than the end of the consumer itself.
"""
from __future__ import annotations

import asyncio
import base64
from typing import Any

from app.core.logging_config import get_logger
from app.services.integrations.tts.streaming import StreamingTTS, StreamingTTSError

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Sentence-boundary thresholds
# ---------------------------------------------------------------------------
# A hard sentence boundary exists when a strong punctuation mark (. ! ?)
# is immediately followed by whitespace (space or newline). Adapted for the
# streaming-delta context where text arrives one character at a time.
_HARD_PUNCT = frozenset(".!?")
_SOFT_PUNCT = frozenset(",;")

# Minimum characters that must be in the buffer before we honour a boundary.
# 5 chars catches short acknowledgements ("OK.", "Oui.") without false-positives.
_MIN_SENTENCE_LEN = 5

# If the buffer exceeds this length and contains a soft-punctuation boundary
# (comma or semicolon) we split there to avoid feeding ElevenLabs an
# excessively long chunk.
_SOFT_SPLIT_THRESHOLD = 120

# Unconditional flush threshold: flush early so Twilio never waits more than
# ~80 chars (~half a sentence) for audio to start. Shorter chunks let
# ElevenLabs start synthesising sooner, cutting perceived response latency.
_FORCE_FLUSH_THRESHOLD = 80


def _detect_sentence_boundary(buffer: str) -> tuple[str, str]:
    """Detect whether *buffer* contains a completed sentence.

    Returns ``(sentence, remainder)`` where *sentence* is the text to
    synthesise now and *remainder* is what stays in the buffer.  If no
    boundary is found both values are ``("", buffer)``.

    Detection rules (in priority order):
    1. Hard boundary: buffer ends with ``<punct><space>`` or
       ``<punct><newline>`` where punct is one of ``. ! ?`` and the
       total length (stripped) is at least ``_MIN_SENTENCE_LEN``.
    2. Soft boundary: buffer length > ``_SOFT_SPLIT_THRESHOLD`` and
       the last occurrence of ``, `` or ``; `` gives a split point.
    3. Force-flush: buffer length > ``_FORCE_FLUSH_THRESHOLD`` with no
       other boundary — flush the whole buffer.
    """
    stripped = buffer.rstrip()

    # Rule 1: hard boundary via trailing punctuation + whitespace.
    # We look at the stripped content so that "Hello. \n" still qualifies.
    if len(stripped) >= _MIN_SENTENCE_LEN and stripped[-1] in _HARD_PUNCT:
        # The full buffer *after* the punctuation becomes the remainder.
        split_pos = buffer.rfind(stripped[-1]) + 1
        sentence = buffer[:split_pos].strip()
        remainder = buffer[split_pos:]
        # Only accept if there is trailing whitespace or the buffer ends here.
        if not remainder or remainder[0] in (" ", "\n", "\r"):
            return sentence, remainder.lstrip(" \n\r")

    # Rule 2: soft boundary when buffer is long.
    if len(buffer) > _SOFT_SPLIT_THRESHOLD:
        for soft_seq in (", ", "; "):
            pos = buffer.rfind(soft_seq)
            if pos != -1 and pos >= _MIN_SENTENCE_LEN:
                sentence = buffer[: pos + 1].strip()   # include the comma/semi
                remainder = buffer[pos + 2:]            # skip the space after
                return sentence, remainder

    # Rule 3: unconditional flush.
    if len(buffer) > _FORCE_FLUSH_THRESHOLD:
        return buffer.strip(), ""

    # No boundary detected.
    return "", buffer


class TTSBridge:
    """Streams text deltas from OpenAI Realtime → ElevenLabs TTS → Twilio Media Stream.

    Lifecycle
    ---------
    1. Instantiate once per call with ``get_tts_bridge()``.
    2. Launch ``tts_consumer(twilio_ws)`` as an ``asyncio.Task`` — it runs
       for the entire call, consuming sentences from the internal queue.
    3. For each OpenAI ``response.text.delta`` event call ``on_text_delta()``.
    4. For the OpenAI ``response.text.done`` event call ``on_text_done()``.
    5. On ``input_audio_buffer.speech_started`` (barge-in) call ``interrupt()``.
    6. Before each new conversation turn call ``reset_for_new_turn()``.
    7. When the call ends, cancel the ``tts_consumer`` task — it handles
       ``asyncio.CancelledError`` cleanly.
    """

    def __init__(
        self,
        tts: StreamingTTS,
        language: str,
        stream_sid: str,
        intent_type: str = "support",
    ) -> None:
        self._tts = tts
        self._language = language
        self._stream_sid = stream_sid
        self._intent_type = intent_type

        # Accumulates raw text deltas between boundary detections.
        self._text_buffer: str = ""

        # Completed sentences waiting to be synthesised.
        # ``None`` is the end-of-turn sentinel.
        self._sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()

        # Barge-in / interruption flag.
        self._interrupted: bool = False

        # Reference to the currently running synthesis task so it can be
        # cancelled on barge-in.
        self._active_tts_task: asyncio.Task[Any] | None = None

    # ------------------------------------------------------------------
    # Public API — called by the media stream handler
    # ------------------------------------------------------------------

    def set_language(self, language: str) -> None:
        """Switch the TTS voice language mid-call ("changement de langue à la volée")."""
        self._language = language

    async def on_text_delta(self, delta: str) -> None:
        """Receive a text delta from OpenAI and enqueue completed sentences.

        Called for every ``response.text.delta`` event.  Appends *delta*
        to the internal buffer, checks for sentence boundaries, and puts
        any completed sentence into ``_sentence_queue``.
        """
        if not delta:
            return

        self._text_buffer += delta

        # Keep extracting sentences as long as boundaries are found.
        # A single delta may push the buffer past multiple boundaries
        # (e.g. receiving "end. Next sentence. And another. ").
        while True:
            sentence, remainder = _detect_sentence_boundary(self._text_buffer)
            if not sentence:
                break
            self._text_buffer = remainder
            logger.debug(
                f"[TTSBridge] Sentence queued ({len(sentence)} chars): "
                f"{sentence[:60]}{'...' if len(sentence) > 60 else ''}"
            )
            await self._sentence_queue.put(sentence)

    async def on_text_done(self, full_text: str) -> None:  # noqa: ARG002
        """Flush remaining buffer and signal end-of-turn.

        Called when OpenAI emits ``response.text.done``.  The *full_text*
        argument is accepted for caller convenience but the bridge relies
        on its own buffer to avoid double-processing.
        """
        remaining = self._text_buffer.strip()
        if remaining:
            logger.debug(
                f"[TTSBridge] Flushing tail ({len(remaining)} chars): "
                f"{remaining[:60]}{'...' if len(remaining) > 60 else ''}"
            )
            await self._sentence_queue.put(remaining)

        self._text_buffer = ""
        # Sentinel: tells tts_consumer this turn is complete.
        await self._sentence_queue.put(None)
        logger.debug("[TTSBridge] End-of-turn sentinel enqueued.")

    async def interrupt(self, twilio_ws: Any) -> None:
        """Handle barge-in: stop TTS and clear queued audio.

        Called when ``input_audio_buffer.speech_started`` fires — the user
        has started speaking over the assistant's response.

        Steps:
        1. Raise the interrupted flag so the active synthesis loop exits.
        2. Discard buffered text.
        3. Drain the sentence queue so no stale sentences are synthesised.
        4. Cancel any in-flight ElevenLabs request.
        5. Tell Twilio to clear its audio buffer.
        6. Reset the flag for the next turn.
        """
        logger.info("[TTSBridge] Barge-in detected — interrupting TTS.")
        self._interrupted = True
        self._text_buffer = ""

        # Drain the queue without blocking.
        drained = 0
        while not self._sentence_queue.empty():
            try:
                self._sentence_queue.get_nowait()
                drained += 1
            except asyncio.QueueEmpty:
                break
        if drained:
            logger.debug(f"[TTSBridge] Drained {drained} sentences from queue.")

        # Cancel the active ElevenLabs streaming task if one is running.
        if self._active_tts_task and not self._active_tts_task.done():
            self._active_tts_task.cancel()
            logger.debug("[TTSBridge] Active TTS task cancelled.")

        # Instruct Twilio to stop playing buffered audio immediately.
        try:
            await twilio_ws.send_json(
                {
                    "event": "clear",
                    "streamSid": self._stream_sid,
                }
            )
            logger.debug("[TTSBridge] Twilio 'clear' event sent.")
        except Exception as exc:
            logger.warning(f"[TTSBridge] Failed to send Twilio 'clear' event: {exc}")

        self._interrupted = False

    def reset_for_new_turn(self) -> None:
        """Reset transient state at the start of a new conversation turn.

        Call this just before OpenAI begins generating a new response.
        The sentence queue should already be empty (the previous turn
        sentinel was consumed), so we do NOT drain it here.
        """
        self._text_buffer = ""
        self._interrupted = False
        logger.debug("[TTSBridge] State reset for new turn.")

    async def wait_until_drained(self, timeout: float = 15.0) -> None:
        """Wait until all queued sentences have been synthesised and sent.

        Used before hangup so the goodbye sentence is never cut off.
        Polls every 100 ms until both conditions hold:
          - The sentence queue is empty (no more text waiting to be sent to ElevenLabs).
          - No ElevenLabs synthesis task is currently in flight.
        Falls back to *timeout* seconds if the queue never drains (safety net).

        Args:
            timeout: Maximum seconds to wait before giving up (default 15 s).
        """
        import asyncio as _asyncio
        deadline = _asyncio.get_event_loop().time() + timeout
        while _asyncio.get_event_loop().time() < deadline:
            queue_empty = self._sentence_queue.empty()
            tts_idle = self._active_tts_task is None or self._active_tts_task.done()
            if queue_empty and tts_idle:
                logger.debug("[TTSBridge] Queue drained — ready to hang up.")
                return
            await _asyncio.sleep(0.1)
        logger.warning(
            f"[TTSBridge] wait_until_drained timed out after {timeout}s "
            "— hanging up anyway."
        )



    # ------------------------------------------------------------------
    # TTS consumer — run as a long-lived asyncio.Task
    # ------------------------------------------------------------------

    async def tts_consumer(self, twilio_ws: Any) -> None:
        """Consume sentences from the queue and stream audio to Twilio.

        This coroutine runs for the **entire call** (not just one turn).
        It exits only when cancelled (call ends).

        Sentence queue protocol
        -----------------------
        - ``str``  → synthesise and send to Twilio.
        - ``None`` → end-of-turn marker; log and continue waiting for
                     the next turn's sentences.

        The loop is cancel-safe: ``asyncio.CancelledError`` is caught,
        logged, and re-raised so the caller's task infrastructure can
        clean up properly.
        """
        logger.info("[TTSBridge] TTS consumer started.")
        try:
            while True:
                sentence = await self._sentence_queue.get()

                # End-of-turn sentinel — this response is complete.
                if sentence is None:
                    logger.info("[TTSBridge] End-of-turn sentinel received — response complete.")
                    continue

                # Skip stale sentences queued before a barge-in.
                if self._interrupted:
                    logger.debug("[TTSBridge] Skipping sentence (interrupted).")
                    continue

                await self._synthesise_and_send(sentence, twilio_ws)

        except asyncio.CancelledError:
            logger.info("[TTSBridge] TTS consumer cancelled — call ended.")
            raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _synthesise_and_send(self, sentence: str, twilio_ws: Any) -> None:
        """Synthesise *sentence* via ElevenLabs and forward chunks to Twilio.

        Handles ``StreamingTTSError`` gracefully (logs and moves on) so
        that a single failed synthesis does not crash the consumer loop.
        Handles ``asyncio.CancelledError`` by breaking out of the chunk
        loop and re-raising so the caller can clean up.
        """
        # Store a reference to the current task so ``interrupt()`` can
        # cancel it.  We use ``asyncio.current_task()`` rather than
        # self-assigning a separate Task object because the consumer
        # itself *is* the running task.
        self._active_tts_task = asyncio.current_task()
        try:
            chunk_count = 0
            async for audio_chunk in self._tts.stream_synthesize(
                sentence,
                language=self._language,
                intent_type=self._intent_type,
                output_format="ulaw_8000",
            ):
                if self._interrupted:
                    logger.debug("[TTSBridge] Synthesis interrupted mid-stream.")
                    break

                audio_b64 = base64.b64encode(audio_chunk).decode("utf-8")
                await twilio_ws.send_json(
                    {
                        "event": "media",
                        "streamSid": self._stream_sid,
                        "media": {"payload": audio_b64},
                    }
                )
                chunk_count += 1

            logger.debug(
                f"[TTSBridge] Sent {chunk_count} audio chunks for: "
                f"{sentence[:60]}{'...' if len(sentence) > 60 else ''}"
            )

        except StreamingTTSError as exc:
            logger.error(f"[TTSBridge] TTS error for sentence '{sentence[:60]}...': {exc}")

        except asyncio.CancelledError:
            logger.debug("[TTSBridge] _synthesise_and_send cancelled.")
            raise

        except Exception as exc:
            logger.error(f"[TTSBridge] Unexpected error during synthesis: {exc}")

        finally:
            self._active_tts_task = None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_tts_bridge(
    tts: StreamingTTS,
    language: str,
    stream_sid: str,
    intent_type: str = "support",
) -> TTSBridge:
    """Construct and return a ``TTSBridge`` for the given call parameters."""
    return TTSBridge(tts, language, stream_sid, intent_type)
