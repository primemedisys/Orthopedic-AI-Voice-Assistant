"""
app/services/summary_service.py
───────────────────────────────
Runs when a call ends: writes a short summary, saves it, and alerts the doctor
if the call needs attention. If the LLM fails, a basic fallback summary is used,
so the doctor still hears about urgent calls.
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select

from app.agent.llm import get_llm
from app.database import get_session
from app.models.call_summary import CallSummary
from app.services import call_service
from app.services.notification_service import notify_doctor

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = (
    "You write call summaries for a busy orthopedic doctor. In 3-5 short lines include: "
    "who called, what the problem is, how long/how severe if mentioned, what was promised "
    "(callback, etc.), and anything the doctor must do. Do not add medical advice."
)


def _build_transcript(messages) -> str:
    """Turn the message list into plain 'Caller: ... / Bot: ...' lines."""
    return "\n".join(
        f"{'Caller' if m.type == 'human' else 'Bot'}: {m.content}"
        for m in messages if m.type in ("human", "ai") and m.content
    )


def _write_summary(transcript: str) -> str:
    """Ask the LLM for a summary. If it fails, return the last few lines of the call instead."""
    try:
        return get_llm().invoke([SystemMessage(SUMMARY_PROMPT), HumanMessage(transcript)]).content
    except Exception:
        logger.exception("❌ [Summary] LLM summary failed, using fallback")
        last_lines = "\n".join(transcript.splitlines()[-6:])
        return f"(Auto-summary unavailable) Last messages:\n{last_lines}"


def finish_call(graph, call_id: str) -> str | None:
    """Summarize a finished call, save it, alert the doctor if needed. Returns None if the call has no messages."""
    # Already summarized? Return the saved one (safe if /end is called twice).
    with get_session() as db:
        existing = db.scalar(select(CallSummary).where(CallSummary.call_id == call_id))
        if existing:
            return existing.summary

    values = graph.get_state({"configurable": {"thread_id": call_id}}).values
    messages = values.get("messages", [])
    if not messages:
        return None

    summary = _write_summary(_build_transcript(messages))
    urgency = values.get("urgency", "routine")
    caller_type = values.get("caller_type")
    phone = values.get("caller_phone", "unknown")

    try:
        with get_session() as db:
            db.add(CallSummary(call_id=call_id, summary=summary, urgency=urgency))
        logger.info("✅ [Summary] Saved for call %s", call_id)
    except Exception:
        logger.error("❌ [Summary] Could not save summary for call %s", call_id)

    call_service.end_call(call_id, caller_type, values.get("patient_id"), urgency)

    # The doctor only hears about calls that matter
    if urgency != "routine" or caller_type == "new":
        level = urgency if urgency in ("emergency", "urgent") else "info"
        notify_doctor(f"Call from {phone} ({caller_type} patient)\n{summary}", level=level)

    return summary