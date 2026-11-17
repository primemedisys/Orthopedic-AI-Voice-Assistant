"""
app/services/notification_service.py
────────────────────────────────────
Sends alerts to the doctor. For now it only logs them in the terminal.
In Phase 2 the WhatsApp/SMS call goes inside notify_doctor(); nothing else changes.
"""

import logging

logger = logging.getLogger("doctor_alerts")

_PREFIX = {
    "emergency": "🚨 EMERGENCY",
    "urgent": "⚠️ URGENT",
    "info": "ℹ️ INFO",
}


def notify_doctor(message: str, level: str = "info") -> bool:
    """Alert the doctor. Returns True if sent, False if it failed (never raises)."""
    try:
        logger.warning("%s | %s", _PREFIX.get(level, _PREFIX["info"]), message)
        return True
    except Exception:
        logger.exception("❌ [Notify] Could not alert the doctor")
        return False