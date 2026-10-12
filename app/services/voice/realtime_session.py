"""
RealtimeSession — manages a single OpenAI Realtime API WebSocket connection for one call.

Connects directly to wss://api.openai.com/v1/realtime using the websockets library.
Audio flows in (mulaw/pcmu from Twilio), text flows out (sent to ElevenLabs TTS).
"""
import asyncio
import json
from typing import Callable, Awaitable, Optional

import websockets
import websockets.exceptions

from app.core.config import get_settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

_LOG_PREFIX = "[RealtimeSession]"

# Default model used when the setting is not present in the environment.
_DEFAULT_REALTIME_MODEL = "gpt-4o-realtime-preview"

# OpenAI Realtime API base URI
_REALTIME_BASE_URI = "wss://api.openai.com/v1/realtime"


class RealtimeConnectionError(Exception):
    """Raised when the WebSocket connection to the OpenAI Realtime API cannot be established."""


# ---------------------------------------------------------------------------
# Async no-op helpers used as default callback implementations
# ---------------------------------------------------------------------------

async def _noop_str(value: str) -> None:  # noqa: D401
    """No-op callback for single-string signatures."""


async def _noop_dict(value: dict) -> None:  # noqa: D401
    """No-op callback for dict signatures."""


async def _noop_void() -> None:  # noqa: D401
    """No-op callback for zero-argument signatures."""


async def _noop_tool(call_id: str, name: str, args_json: str) -> None:  # noqa: D401
    """No-op callback for tool-call signatures."""


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class RealtimeSession:
    """Manages a single OpenAI Realtime API WebSocket connection for one call.

    Usage::

        session = RealtimeSession(system_prompt, tools, language="fr")
        session.on_text_done = my_tts_handler
        session.on_tool_call = my_tool_handler
        await session.connect()

        # Run concurrently with media-stream reader
        asyncio.create_task(session.receive_loop())

        # Forward audio chunks from Twilio
        await session.send_audio(base64_payload)

        # When done
        await session.disconnect()
    """

    def __init__(
        self,
        system_prompt: str,
        tools: list[dict],
        language: str,
        temperature: float = 0.7,
    ) -> None:
        self._system_prompt = system_prompt
        self._tools = tools
        self._language = language
        self._temperature = temperature

        # Private WebSocket handle — set in connect()
        self._ws: Optional[websockets.WebSocketClientProtocol] = None  # type: ignore[type-arg]

        # Resolve config values at construction time so connect() is fast.
        settings = get_settings()
        self._api_key: str = settings.openai_api_key
        # openai_realtime_model may not exist in the Settings class yet; fall back gracefully.
        self._model: str = getattr(settings, "openai_realtime_model", _DEFAULT_REALTIME_MODEL)

        # ------------------------------------------------------------------
        # Callbacks — replaced by the media-stream handler before connect().
        # All are async callables so they can do I/O (e.g. write to TTS).
        # ------------------------------------------------------------------

        # Called with each incremental text token as GPT streams the reply.
        self.on_text_delta: Callable[[str], Awaitable[None]] = _noop_str

        # Called once when the full assistant text response is complete.
        self.on_text_done: Callable[[str], Awaitable[None]] = _noop_str

        # Called when GPT wants to invoke a function tool.
        # Signature: (call_id: str, function_name: str, arguments_json: str)
        self.on_tool_call: Callable[[str, str, str], Awaitable[None]] = _noop_tool

        # Called when the VAD detects the caller started speaking (barge-in).
        self.on_speech_started: Callable[[], Awaitable[None]] = _noop_void

        # Called when VAD detects the end of the caller's utterance.
        self.on_speech_stopped: Callable[[], Awaitable[None]] = _noop_void

        # Called with the Whisper transcript of the caller's utterance.
        self.on_transcript: Callable[[str], Awaitable[None]] = _noop_str

        # Called on any error event from the OpenAI API.
        self.on_error: Callable[[dict], Awaitable[None]] = _noop_dict

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the WebSocket to the OpenAI Realtime API and configure the session.

        Raises:
            RealtimeConnectionError: if the WebSocket handshake fails.
        """
        uri = f"{_REALTIME_BASE_URI}?model={self._model}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Beta": "realtime=v1",
        }

        logger.info(f"{_LOG_PREFIX} Connecting to {uri}")

        try:
            # websockets 12.x: connect() returns an async context manager.
            # We enter it manually so the connection lives beyond this method.
            self._ws = await websockets.connect(uri, extra_headers=headers)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.error(f"{_LOG_PREFIX} WebSocket connection failed: {exc}")
            raise RealtimeConnectionError(
                f"Failed to connect to OpenAI Realtime API: {exc}"
            ) from exc

        logger.info(f"{_LOG_PREFIX} Connected. Sending session.update …")
        await self._send_session_update()
        logger.info(f"{_LOG_PREFIX} session.update sent")

    async def send_audio(self, payload: str) -> None:
        """Forward a base64-encoded mulaw audio chunk from Twilio to OpenAI.

        Args:
            payload: Base64-encoded PCM-u (mulaw) audio data as produced by Twilio
                     Media Streams.
        """
        if self._ws is None:
            logger.warning(f"{_LOG_PREFIX} send_audio called before connect()")
            return

        message = json.dumps({"type": "input_audio_buffer.append", "audio": payload})
        try:
            await self._ws.send(message)
        except websockets.exceptions.ConnectionClosed as exc:
            logger.warning(f"{_LOG_PREFIX} Cannot send audio — connection closed: {exc}")
        except Exception as exc:
            logger.error(f"{_LOG_PREFIX} Unexpected error in send_audio: {exc}")

    async def receive_loop(self) -> None:
        """Read events from the OpenAI Realtime API and dispatch to callbacks.

        Runs until the WebSocket closes. Designed to be executed as a background
        asyncio task::

            asyncio.create_task(session.receive_loop())

        Exits gracefully on connection close — does NOT raise.
        """
        if self._ws is None:
            logger.error(f"{_LOG_PREFIX} receive_loop called before connect()")
            return

        logger.info(f"{_LOG_PREFIX} receive_loop started")

        try:
            async for raw_message in self._ws:
                try:
                    event = json.loads(raw_message)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        f"{_LOG_PREFIX} Received non-JSON message, skipping: {exc}"
                    )
                    continue

                await self._dispatch_event(event)

        except websockets.exceptions.ConnectionClosedError as exc:
            logger.warning(f"{_LOG_PREFIX} WebSocket closed with error: {exc}")
        except Exception as exc:
            logger.error(f"{_LOG_PREFIX} Unexpected error in receive_loop: {exc}", exc_info=True)

        logger.info(f"{_LOG_PREFIX} receive_loop finished")

    async def send_tool_result(
        self,
        call_id: str,
        result: str,
        trigger_response: bool = True,
    ) -> None:
        """Send a function call result back to OpenAI.

        Args:
            call_id:          The ``call_id`` from the
                              ``response.function_call_arguments.done`` event.
            result:           JSON-serialisable string produced by the tool.
            trigger_response: If True (default) send ``response.create`` so
                              OpenAI generates a follow-up reply.  Set to
                              False for terminal tools (e.g. ``end_call``)
                              where no further model output is desired.
        """
        if self._ws is None:
            logger.warning(f"{_LOG_PREFIX} send_tool_result called before connect()")
            return

        create_item = json.dumps(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": result,
                },
            }
        )

        try:
            await self._ws.send(create_item)
            if trigger_response:
                trigger = json.dumps({"type": "response.create"})
                await self._ws.send(trigger)
            logger.debug(
                f"{_LOG_PREFIX} Sent tool result for call_id={call_id!r} "
                f"(trigger_response={trigger_response})"
            )
        except websockets.exceptions.ConnectionClosed as exc:
            logger.warning(
                f"{_LOG_PREFIX} Cannot send tool result — connection closed: {exc}"
            )
        except Exception as exc:
            logger.error(f"{_LOG_PREFIX} Unexpected error in send_tool_result: {exc}")

    async def inject_user_turn(self, text: str) -> None:
        """Inject a simulated user text message into the conversation and request a response."""
        if self._ws is None:
            return
        logger.info(f"{_LOG_PREFIX} Injecting user turn: {text!r}")
        item = {
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            },
        }
        try:
            await self._ws.send(json.dumps(item))
            await self._ws.send(json.dumps({"type": "response.create"}))
        except Exception as exc:
            logger.warning(f"{_LOG_PREFIX} Error injecting turn: {exc}")

    async def trigger_initial_response(self) -> None:
        """Send response.create to kick off the assistant's opening greeting.

        Must be called after connect() and after the receive_loop task is
        running.  Without this, server-VAD mode waits for the caller to speak
        first — resulting in silence on answer.
        """
        if self._ws is None:
            logger.warning(f"{_LOG_PREFIX} trigger_initial_response called before connect()")
            return
        try:
            await self._ws.send(json.dumps({"type": "response.create"}))
            logger.info(f"{_LOG_PREFIX} Initial response.create sent — waiting for greeting")
        except websockets.exceptions.ConnectionClosed as exc:
            logger.warning(f"{_LOG_PREFIX} Cannot trigger initial response — connection closed: {exc}")
        except Exception as exc:
            logger.error(f"{_LOG_PREFIX} Unexpected error in trigger_initial_response: {exc}")

    async def disconnect(self) -> None:
        """Close the WebSocket connection cleanly.

        Never raises — safe to call even if already disconnected.
        """
        if self._ws is None:
            return
        try:
            await self._ws.close()
            logger.info(f"{_LOG_PREFIX} WebSocket closed by disconnect()")
        except Exception as exc:
            # Log but swallow — callers should not need to handle disconnect errors.
            logger.warning(f"{_LOG_PREFIX} Error while closing WebSocket: {exc}")
        finally:
            self._ws = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def update_language(self, language: str) -> None:
        """Switch the conversation language mid-call ("changement de langue à la volée").

        Re-sends session.update with instructions for the new language. The
        caller is responsible for also updating the TTS voice for the new
        language (the Realtime session only affects what the model says, not
        which ElevenLabs voice speaks it).
        """
        self._language = language
        if self._ws is None:
            logger.warning(f"{_LOG_PREFIX} update_language called before connect()")
            return
        await self._send_session_update()
        logger.info(f"{_LOG_PREFIX} Session language switched to {language!r}")

    async def _send_session_update(self) -> None:
        """Send the session.update configuration event to the Realtime API."""
        _LANGUAGE_NAMES = {
            "fr": "French (fr-FR)",
            "en": "English (en-US)",
            "ar": "Arabic",
            "it": "Italian",
            "es": "Spanish",
        }
        instructions = self._system_prompt
        language_name = _LANGUAGE_NAMES.get(self._language)
        if language_name:
            instructions += f"\n\nLanguage: {language_name}. Respond only in this language."

        session_update = {
            "type": "session.update",
            "session": {
                "modalities": ["text"],
                "instructions": instructions,
                "input_audio_format": "g711_ulaw",
                "output_audio_format": "g711_ulaw",
                "input_audio_transcription": {"model": "whisper-1"},
                "input_audio_noise_reduction": {"type": "near_field"},  # suppress line echo/sidetone
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.75,         # balanced: rejects ambient noise while catching
                                               # quieter or softer-spoken patients on PSTN
                    "prefix_padding_ms": 200,  # keep minimal for fast response
                    "silence_duration_ms": 300, # 300ms is the sweet spot for natural phone cadence;
                                               # 500ms added noticeable lag between patient and the assistant
                },
                "tools": self._tools,
                "tool_choice": "auto",
                "temperature": self._temperature,
            },
        }
        assert self._ws is not None, "_send_session_update called before connect()"
        await self._ws.send(json.dumps(session_update))

    async def _dispatch_event(self, event: dict) -> None:
        """Route a parsed OpenAI event to the appropriate handler or callback."""
        event_type: str = event.get("type", "")

        # ------------------------------------------------------------------
        # Informational / lifecycle events
        # ------------------------------------------------------------------
        if event_type in ("session.created", "session.updated"):
            logger.info(f"{_LOG_PREFIX} Event: {event_type}")

        elif event_type == "input_audio_buffer.speech_started":
            logger.info(f"{_LOG_PREFIX} Speech started (barge-in signal)")
            await self._safe_callback(self.on_speech_started)

        elif event_type == "input_audio_buffer.speech_stopped":
            logger.info(f"{_LOG_PREFIX} Speech stopped")
            await self._safe_callback(self.on_speech_stopped)

        # ------------------------------------------------------------------
        # Transcript (caller utterance recognised by Whisper)
        # ------------------------------------------------------------------
        elif event_type == "conversation.item.input_audio_transcription.completed":
            transcript_text: str = (
                event.get("transcript", "")
                or event.get("item", {}).get("transcript", "")
            )
            logger.info(
                f"{_LOG_PREFIX} Transcript completed: {transcript_text!r}"
            )
            await self._safe_callback(self.on_transcript, transcript_text)

        # ------------------------------------------------------------------
        # Streaming assistant text response
        # ------------------------------------------------------------------
        elif event_type == "response.text.delta":
            delta_text: str = event.get("delta", "")
            await self._safe_callback(self.on_text_delta, delta_text)

        elif event_type == "response.text.done":
            full_text: str = event.get("text", "")
            logger.debug(f"{_LOG_PREFIX} response.text.done: {full_text!r}")
            await self._safe_callback(self.on_text_done, full_text)

        # ------------------------------------------------------------------
        # Function / tool call
        # ------------------------------------------------------------------
        elif event_type == "response.function_call_arguments.done":
            call_id: str = event.get("call_id", "")
            function_name: str = event.get("name", "")
            arguments_json: str = event.get("arguments", "{}")
            logger.info(
                f"{_LOG_PREFIX} Tool call: {function_name!r} "
                f"call_id={call_id!r}"
            )
            await self._safe_callback(
                self.on_tool_call, call_id, function_name, arguments_json
            )

        # ------------------------------------------------------------------
        # Response lifecycle
        # ------------------------------------------------------------------
        elif event_type == "response.done":
            logger.info(f"{_LOG_PREFIX} Response completed")

        # ------------------------------------------------------------------
        # Errors
        # ------------------------------------------------------------------
        elif event_type == "error":
            error_dict: dict = event.get("error", event)
            logger.error(f"{_LOG_PREFIX} API error event: {error_dict}")
            await self._safe_callback(self.on_error, error_dict)

        # ------------------------------------------------------------------
        # Unhandled — log at DEBUG so we don't spam the logs
        # ------------------------------------------------------------------
        else:
            logger.debug(f"{_LOG_PREFIX} Unhandled event type: {event_type!r}")

    async def _safe_callback(self, callback: Callable, /, *args) -> None:  # type: ignore[type-arg]
        """Invoke a callback, catching all exceptions so the receive loop is never crashed."""
        try:
            await callback(*args)
        except Exception as exc:
            logger.error(
                f"{_LOG_PREFIX} Exception in callback {callback!r}: {exc}",
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------

def get_realtime_session(
    system_prompt: str,
    tools: list[dict],
    language: str,
    temperature: float = 0.7,
) -> RealtimeSession:
    """Construct and return a :class:`RealtimeSession`.

    This factory exists so callers can retrieve a fully-initialised session
    without knowing the constructor signature, and to keep the door open for
    future dependency-injection / mocking.

    Args:
        system_prompt: The system instructions sent to the model on session start.
        tools:         OpenAI function-calling tool definitions (list of dicts).
        language:      BCP-47 language code for the call (e.g. ``"fr"``, ``"en"``).
        temperature:   Sampling temperature for the LLM (0.0 – 2.0).

    Returns:
        A configured :class:`RealtimeSession` ready to be connected via
        :meth:`RealtimeSession.connect`.
    """
    return RealtimeSession(
        system_prompt=system_prompt,
        tools=tools,
        language=language,
        temperature=temperature,
    )
