"""TTS integrations."""
from app.services.integrations.tts.client import TTSService, TTSError
from app.services.integrations.tts.streaming import StreamingTTS, StreamingTTSError, get_streaming_tts

__all__ = [
    "TTSService",
    "TTSError",
    "StreamingTTS",
    "StreamingTTSError",
    "get_streaming_tts",
]
