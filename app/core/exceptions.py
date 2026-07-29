"""Shared exception hierarchy for the AI assistant backend."""

class AppError(Exception):
    """Base error for domain-level failures."""


class IntegrationError(AppError):
    """Raised for external integration failures (telephony, STT/TTS, etc.)."""


class ValidationError(AppError):
    """Raised for invalid payloads or state."""


class SecurityError(AppError):
    """Raised when signature/JWT checks fail."""
