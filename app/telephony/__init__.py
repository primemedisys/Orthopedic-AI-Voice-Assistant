"""
app/telephony/__init__.py
─────────────────────────
get_adapter() returns the provider chosen by TELEPHONY_PROVIDER in .env.
"""

from functools import lru_cache

from app.config import settings
from app.telephony.base import TelephonyAdapter
from app.telephony.plivo.plivo_adapter import PlivoAdapter
from app.telephony.twilio.twilio_adapter import TwilioAdapter

@lru_cache
def get_adapter() -> TelephonyAdapter:
    """Create the phone adapter once and reuse it."""
    provider = settings.TELEPHONY_PROVIDER.lower()
    if provider == "plivo":
        return PlivoAdapter()
    if provider == "twilio":
        return TwilioAdapter()
    raise RuntimeError(f"Unsupported TELEPHONY_PROVIDER: {settings.TELEPHONY_PROVIDER}")