"""Twilio telephony adapter: places and terminates calls."""
from typing import Optional
from twilio.rest import Client

from app.core.config import get_settings
from app.core.logging_config import get_logger

settings = get_settings()
logger = get_logger(__name__)


class TelephonyProvider:
    """Thin wrapper around the Twilio REST API for call control."""

    def __init__(self):
        self.account_sid = settings.twilio_account_sid
        self.auth_token = settings.twilio_auth_token
        self.from_number = settings.twilio_phone_number
        self._client = Client(self.account_sid, self.auth_token)

    async def initiate_outbound_call(
        self,
        to_number: str,
        callback_url: str,
        status_callback_url: Optional[str] = None,
    ) -> Optional[str]:
        """Place an outbound call. Twilio hits `callback_url` once the call connects.

        Returns the call SID if successful, None otherwise.
        """
        try:
            call = self._client.calls.create(
                to=to_number,
                from_=self.from_number,
                url=callback_url,
                status_callback=status_callback_url,
                status_callback_event=["initiated", "ringing", "answered", "completed"],
                record=True,
            )
            return call.sid
        except Exception as e:
            logger.error(f"Twilio call initiation failed: {e}")
            return None

    async def hangup_call(self, call_sid: str) -> bool:
        """Hang up an active call."""
        try:
            self._client.calls(call_sid).update(status="completed")
            return True
        except Exception as e:
            logger.error(f"Twilio hangup failed: {e}")
            return False


def get_telephony_provider() -> TelephonyProvider:
    return TelephonyProvider()
