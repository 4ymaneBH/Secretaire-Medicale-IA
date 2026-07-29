"""Streaming TTS for real-time audio generation using ElevenLabs.

Streams audio chunks from the ElevenLabs /stream endpoint so Twilio
can start playing audio before the full synthesis completes.
Target: <500ms to first audio byte.
"""
from typing import AsyncGenerator, Optional, Dict, Any
import httpx
from app.core.config import get_settings
from app.core.logging_config import get_logger

settings = get_settings()
logger = get_logger(__name__)


class StreamingTTSError(Exception):
    """Error during streaming TTS."""
    pass


def _get_voice_settings_for_intent(intent_type: Optional[str]) -> tuple[Dict[str, Any], float]:
    """Return (voice_settings, speed) tuned for the given intent.

    Mirrors the prosody logic in TTSService.synthesize_speech() so that
    streamed audio sounds identical to the non-streaming path.
    """
    if intent_type in ("empathy", "apology", "concern", "technical_issue", "patient_care"):
        return {
            "stability": 0.20,           # More organic micro-variations (breathing, warmth)
            "similarity_boost": 0.75,
            "style": 0.15,
            "use_speaker_boost": True,
        }, 0.95                          # Slower, warmer for empathetic moments

    if intent_type in ("alert", "missed_dose", "low_battery", "urgent", "reminder"):
        return {
            "stability": 0.38,
            "similarity_boost": 0.80,
            "style": 0.35,
            "use_speaker_boost": True,
        }, 1.00                          # Natural pace — not rushed even for alerts

    if intent_type in ("success", "resolution", "greeting", "confirmation", "farewell"):
        return {
            "stability": 0.38,
            "similarity_boost": 0.75,
            "style": 0.55,
            "use_speaker_boost": True,
        }, 1.00                          # Warm and friendly, not fast

    if intent_type in ("support", "explanation", "information", "question"):
        return {
            "stability": 0.30,
            "similarity_boost": 0.75,
            "style": 0.40,
            "use_speaker_boost": True,
        }, 1.00                          # Comfortable, easy-to-follow pace

    # Default neutral
    return {
        "stability": 0.25,               # Natural micro-variations
        "similarity_boost": 0.75,
        "style": 0.35,
        "use_speaker_boost": True,
    }, 1.00                              # Natural baseline — no artificial speed-up


class StreamingTTS:
    """Streaming Text-to-Speech service using ElevenLabs API.

    Streams audio chunks in real-time for reduced time-to-first-byte.
    Target: <500ms latency before user hears first audio.
    """

    def __init__(self):
        self.api_key = settings.elevenlabs_api_key
        self.model = settings.elevenlabs_model
        self.voice_id_fr = settings.elevenlabs_voice_id_fr
        self.voice_id_en = settings.elevenlabs_voice_id_en
        self.base_url = "https://api.elevenlabs.io/v1"
        # Persistent client — reused across all sentences in a call to avoid
        # re-establishing the TCP+TLS connection for every synthesis request.
        self._client = httpx.AsyncClient(timeout=30.0)

    def _get_voice_id(self, language: str) -> str:
        """Get voice ID for language."""
        return settings.get_voice_id(language)

    def _prepare_text(self, text: str, language: str) -> str:
        """Hook for per-language pronunciation fixes before synthesis."""
        return text

    async def stream_synthesize(
        self,
        text: str,
        language: str = "fr",
        intent_type: Optional[str] = None,
        voice_settings: Optional[Dict[str, Any]] = None,
        chunk_size: int = 512,  # 512 bytes ≈ 64ms of ulaw_8000 audio (vs 128ms at 1024)
        output_format: str = "mp3_44100_128",
    ) -> AsyncGenerator[bytes, None]:
        """Stream audio synthesis from ElevenLabs API.

        Args:
            text: Text to synthesize.
            language: Language code (fr/en).
            intent_type: Intent for dynamic prosody (empathy, alert, etc.).
            voice_settings: Optional voice settings override.
            chunk_size: Bytes per chunk (default 1024).
            output_format: ElevenLabs output format. Use ``ulaw_8000`` for
                Twilio Media Streams, ``mp3_44100_128`` for direct playback.

        Yields:
            Audio chunks as bytes in the requested format.
        """
        if not text.strip():
            logger.warning("[StreamingTTS] Empty text, skipping")
            return

        voice_id = self._get_voice_id(language)
        effective_text = self._prepare_text(text, language)

        # Resolve voice settings and speed
        if voice_settings:
            settings_payload = voice_settings
            speech_speed = 1.10
        else:
            settings_payload, speech_speed = _get_voice_settings_for_intent(intent_type)

        payload = {
            "text": effective_text,
            "model_id": self.model,
            "voice_settings": settings_payload,
            "speed": speech_speed,
        }

        accept = "audio/basic" if output_format.startswith("ulaw") else "audio/mpeg"
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": accept,
        }

        url = f"{self.base_url}/text-to-speech/{voice_id}/stream?output_format={output_format}"

        try:
            async with self._client.stream(
                "POST",
                url,
                json=payload,
                headers=headers,
            ) as response:
                if response.status_code != 200:
                    error_msg = await response.aread()
                    logger.error(f"[StreamingTTS] API error: {response.status_code} - {error_msg[:200]}")
                    raise StreamingTTSError(f"ElevenLabs API error: {response.status_code}")

                chunk_count = 0
                total_bytes = 0

                async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                    chunk_count += 1
                    total_bytes += len(chunk)
                    yield chunk

                logger.info(
                    f"[StreamingTTS] Streamed {chunk_count} chunks, "
                    f"{total_bytes} bytes for '{text[:50]}...'"
                )

        except httpx.TimeoutException:
            logger.error("[StreamingTTS] Request timeout")
            raise StreamingTTSError("TTS request timed out")
        except StreamingTTSError:
            raise
        except Exception as e:
            logger.error(f"[StreamingTTS] Error: {e}")
            raise StreamingTTSError(f"TTS streaming failed: {e}")

    async def close(self) -> None:
        """Close the persistent HTTP client. Call when the call ends."""
        await self._client.aclose()

    async def warmup(self) -> None:
        """Pre-warm the TCP+TLS connection to ElevenLabs.

        Sends a cheap GET request to the ElevenLabs models endpoint so
        the HTTP keep-alive connection is already established before the
        first TTS synthesis request.  This eliminates the ~200-400 ms
        TCP+TLS handshake delay that otherwise appears on the assistant's very
        first sentence of the call.

        Safe to call as an asyncio background task — errors are swallowed.
        """
        try:
            url = f"{self.base_url}/models"
            await self._client.get(
                url,
                headers={"xi-api-key": self.api_key},
                timeout=5.0,
            )
            logger.debug("[StreamingTTS] ElevenLabs connection pre-warmed ✓")
        except Exception as exc:
            # Non-fatal — if warmup fails the first real request still works
            logger.debug(f"[StreamingTTS] Warmup skipped: {exc}")



    async def synthesize_to_bytes(
        self,
        text: str,
        language: str = "fr",
        intent_type: Optional[str] = None,
        voice_settings: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        """Stream and collect all audio into bytes.

        Convenience method when full audio is needed.
        """
        chunks = []
        async for chunk in self.stream_synthesize(
            text, language, intent_type=intent_type, voice_settings=voice_settings
        ):
            chunks.append(chunk)
        return b"".join(chunks)


def get_streaming_tts() -> StreamingTTS:
    """Factory for StreamingTTS."""
    return StreamingTTS()
