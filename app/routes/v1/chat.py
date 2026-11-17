"""
app/routes/chat.py
──────────────────
Text-only test endpoints. They call the same agent the phone will use,
so you can test the logic without making calls.

Flow: /chat/start -> /chat/message (repeat) -> /chat/end
"""

import logging
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

# from app.agent.loader import load_replies
from app.agent.loader import pick_reply
from app.services import call_service, patient_service, summary_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


# ==================================================
# SCHEMAS
# ==================================================


class StartIn(BaseModel):
    phone: str


class MessageIn(BaseModel):
    call_id: str
    phone: str
    text: str


class EndIn(BaseModel):
    call_id: str


class APIResponse(BaseModel):
    status: str = "success"
    message: str
    data: Optional[dict] = None


# ==================================================
# HELPERS
# ==================================================


def _clean_phone(raw: str) -> str:
    """Normalize a phone number to E.164 or raise 400 if it is not usable."""
    phone = patient_service.normalize_phone(raw)
    if not phone.startswith("+"):
        raise HTTPException(status_code=400, detail="❌ Invalid phone number. Use format +91XXXXXXXXXX")
    return phone


# ==================================================
# ENDPOINTS
# ==================================================


@router.post("/start")
def start(body: StartIn) -> APIResponse:
    """Begin a test call: returns a new call_id and the greeting."""
    phone = _clean_phone(body.phone)
    call_id = uuid4().hex
    call_service.start_call(call_id, phone)
    logger.info("✅ [Chat] Call started: %s", call_id)
    return APIResponse(
        message="✅ Call started",
        # data={"call_id": call_id, "reply": load_replies()["greeting"]},
        data={"call_id": call_id, "reply": pick_reply("greeting")},
    )


@router.post("/message")
def message(body: MessageIn, request: Request) -> APIResponse:
    """Send one caller message to the agent and return its reply. action='transfer' means connect to the doctor."""
    phone = _clean_phone(body.phone)
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="❌ Message text is empty")

    try:
        result = request.app.state.graph.invoke(
            {"messages": [("user", text)], "caller_phone": phone, "action": None},
            config={"configurable": {"thread_id": body.call_id, "caller_phone": phone}},
        )
    except Exception:
        logger.exception("❌ [Chat] Agent failed for call %s", body.call_id)
        raise HTTPException(status_code=502, detail="❌ The agent failed to respond. Please try again.")

    return APIResponse(
        message="✅ Reply generated",
        data={
            "reply": result["messages"][-1].content,
            "urgency": result.get("urgency"),
            "verified": result.get("is_verified"),
            "action": result.get("action"),
        },
    )


@router.post("/end")
def end(body: EndIn, request: Request) -> APIResponse:
    """End the test call: saves the summary and alerts the doctor if the call needs attention."""
    try:
        summary = summary_service.finish_call(request.app.state.graph, body.call_id)
    except Exception:
        logger.exception("❌ [Chat] Could not finish call %s", body.call_id)
        raise HTTPException(status_code=500, detail="❌ Could not finish the call. Please try again.")

    if summary is None:
        raise HTTPException(status_code=404, detail="❌ No conversation found for this call_id")

    return APIResponse(message="✅ Call ended and summary saved", data={"summary": summary})