"""STT integrations."""
from app.services.integrations.stt.client import STTService, STTError, get_stt_service

__all__ = [
    "STTService",
    "STTError",
    "get_stt_service",
]
