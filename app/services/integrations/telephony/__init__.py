"""Telephony integrations."""
from app.services.integrations.telephony.provider import TelephonyProvider, get_telephony_provider

__all__ = [
    "TelephonyProvider",
    "get_telephony_provider",
]
