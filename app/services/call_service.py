"""
app/services/call_service.py
────────────────────────────
Saves call logs and callback requests.
A DB failure here is logged but never crashes the live call.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import get_session
from app.models.call_logs import CallLog
from app.models.callback import Callback

logger = logging.getLogger(__name__)


def start_call(call_id: str, phone: str) -> None:
    """Create the call_logs row when a call begins."""
    try:
        with get_session() as db:
            db.add(CallLog(call_id=call_id, caller_phone=phone))
    except Exception:
        logger.error("❌ [CallService] Could not log call start: %s", call_id)


def end_call(call_id: str, caller_type: str | None, patient_id: int | None, urgency: str | None) -> None:
    """Fill in the final details of the call_logs row when the call ends."""
    try:
        with get_session() as db:
            log = db.scalar(select(CallLog).where(CallLog.call_id == call_id))
            if log:
                log.caller_type = caller_type
                log.patient_id = patient_id
                log.urgency = urgency
                log.ended_at = datetime.now(timezone.utc)
    except Exception:
        logger.error("❌ [CallService] Could not log call end: %s", call_id)


def create_callback(
    call_id: str,
    phone: str,
    issue: str,
    urgency: str,
    name: str | None = None,
    date_of_birth: str | None = None,
) -> bool:
    """Save a callback request for the doctor's team. Returns True if saved, False if it failed."""
    try:
        with get_session() as db:
            db.add(Callback(call_id=call_id, name=name, phone=phone,
                            date_of_birth=date_of_birth, issue=issue, urgency=urgency))
        logger.info("✅ [CallService] Callback saved (%s) for call %s", urgency, call_id)
        return True
    except Exception:
        logger.error("❌ [CallService] Callback NOT saved for call %s", call_id)
        return False