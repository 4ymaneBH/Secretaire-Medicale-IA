"""Telephony provider adapter (Twilio/Plivo) for outbound/inbound calls."""
from typing import Dict, Any, Optional
from app.core.config import get_settings
from app.core.logging_config import get_logger

settings = get_settings()
logger = get_logger(__name__)


class TelephonyProvider:
    """Adapter for telephony providers (Twilio, Plivo, etc.)."""

    def __init__(self):
        self.provider = "twilio"  # Default to Twilio
        self.account_sid = settings.twilio_account_sid
        self.auth_token = settings.twilio_auth_token
        self.from_number = settings.twilio_phone_number
    
    async def initiate_outbound_call(
        self, 
        to_number: str, 
        callback_url: str,
        status_callback_url: Optional[str] = None
    ) -> Optional[str]:
        """
        Initiate an outbound call.
        
        Returns:
            Call SID if successful, None otherwise
        """
        if self.provider == "twilio":
            return await self._twilio_initiate_call(to_number, callback_url, status_callback_url)
        elif self.provider == "plivo":
            return await self._plivo_initiate_call(to_number, callback_url, status_callback_url)
        else:
            logger.error(f"Unsupported telephony provider: {self.provider}")
            return None
    
    async def _twilio_initiate_call(
        self, 
        to_number: str, 
        callback_url: str, 
        status_callback_url: Optional[str]
    ) -> Optional[str]:
        """Initiate call using Twilio."""
        try:
            from twilio.rest import Client
            
            client = Client(self.account_sid, self.auth_token)
            call = client.calls.create(
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
    
    async def _plivo_initiate_call(
        self, 
        to_number: str, 
        callback_url: str, 
        status_callback_url: Optional[str]
    ) -> Optional[str]:
        """Initiate call using Plivo."""
        try:
            import plivo
            
            client = plivo.RestClient(self.account_sid, self.auth_token)
            response = client.calls.create(
                from_=self.from_number,
                to_=to_number,
                answer_url=callback_url,
                answer_method="POST"
            )
            return response[1].get("request_uuid")
        except Exception as e:
            logger.error(f"Plivo call initiation failed: {e}")
            return None
    
    async def hangup_call(self, call_sid: str) -> bool:
        """Hangup an active call."""
        if self.provider == "twilio":
            return await self._twilio_hangup(call_sid)
        elif self.provider == "plivo":
            return await self._plivo_hangup(call_sid)
        return False
    
    async def _twilio_hangup(self, call_sid: str) -> bool:
        """Hangup Twilio call."""
        try:
            from twilio.rest import Client
            
            client = Client(self.account_sid, self.auth_token)
            client.calls(call_sid).update(status="completed")
            return True
        except Exception as e:
            logger.error(f"Twilio hangup failed: {e}")
            return False
    
    async def _plivo_hangup(self, call_uuid: str) -> bool:
        """Hangup Plivo call."""
        try:
            import plivo
            
            client = plivo.RestClient(self.account_sid, self.auth_token)
            client.calls.hangup(call_uuid)
            return True
        except Exception as e:
            logger.error(f"Plivo hangup failed: {e}")
            return False
