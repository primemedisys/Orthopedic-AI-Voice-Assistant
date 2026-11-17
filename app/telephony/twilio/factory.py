from functools import lru_cache

from app.config import get_settings
from app.telephony.base import TelephonyAdapter
from app.telephony.twilio.twilio_adapter import TwilioAdapter

@lru_cache
def get_telephony() -> TelephonyAdapter:
    """Return the adapter for the configured provider (created once)."""
    settings = get_settings()
    if settings.telephony_provider == "twilio":

        return TwilioAdapter(settings)
    raise ValueError(f"Unsupported TELEPHONY_PROVIDER: {settings.telephony_provider}")