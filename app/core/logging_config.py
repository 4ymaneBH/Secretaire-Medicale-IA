"""Structured JSON logging with call-SID context tracking."""
import logging
import sys
import hashlib
from typing import Optional
from contextvars import ContextVar
import structlog
from pythonjsonlogger import jsonlogger

call_context: ContextVar[Optional[str]] = ContextVar("call_context", default=None)


def hash_phone_number(phone: str) -> str:
    """Hash phone number for anonymization."""
    return hashlib.sha256(phone.encode()).hexdigest()[:16]


def configure_logging(
    log_level: str = "INFO",
    log_format: str = "json",
):
    """Configure application logging."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(message)s",
        stream=sys.stdout,
    )

    if log_format == "json":
        log_handler = logging.StreamHandler()
        formatter = jsonlogger.JsonFormatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s %(call_sid)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
        log_handler.setFormatter(formatter)
        root_logger = logging.getLogger()
        root_logger.handlers = [log_handler]

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a logger instance."""
    return structlog.get_logger(name)


def set_call_context(call_sid: str):
    """Set call SID in context for logging."""
    call_context.set(call_sid)


def get_call_context() -> Optional[str]:
    """Get current call SID from context."""
    return call_context.get()


def clear_call_context():
    """Clear call context."""
    call_context.set(None)


class CallContextFilter(logging.Filter):
    """Add call SID to log records."""

    def filter(self, record):
        record.call_sid = get_call_context() or "no-call"
        return True


logging.getLogger().addFilter(CallContextFilter())
