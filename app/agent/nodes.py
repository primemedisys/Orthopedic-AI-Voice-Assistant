"""
app/agent/nodes.py
──────────────────
The steps of the graph. Each node receives the state and returns the changes to make.
Order on every caller message:
    lookup_patient -> urgency_check -> (escalate | verify_identity | assistant)

Error rule: a node never crashes the call. On failure it logs and returns a safe reply.
Fixed lines come from pick_reply(), which rotates between approved variants.
"""

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, trim_messages, ToolMessage # type: ignore
from langchain_core.runnables import RunnableConfig # type: ignore
from pydantic import BaseModel, Field

from app.agent.llm import get_llm
from app.agent.loader import pick_reply
from app.agent.prompts import build_system_prompt
from app.agent.rules import classify
from app.agent.state import CallState
from app.agent.tools import TOOLS
from app.database import get_session
from app.models.patient import Patient
from app.services import call_service, patient_service
from app.services.notification_service import notify_doctor

logger = logging.getLogger(__name__)

MAX_VERIFY_ATTEMPTS = 2
RANK = { "routine": 0, "urgent": 1, "emergency": 2 }

# ==================================================
# HELPERS
# ==================================================

def _last_user_text(state: CallState) -> str:
    """Return the caller's latest message text."""
    for m in reversed(state["messages"]):
        if isinstance(m, HumanMessage):
            return m.content
    return ""

# ==================================================
# NODES
# ==================================================

def lookup_patient(state: CallState):
    """Find out who is calling (once per call). If the DB fails, treat them as NEW so nothing private is revealed."""
    if state.get("caller_type"):
        return {"caller_type": state["caller_type"]}

    phone = patient_service.normalize_phone(state.get("caller_phone", ""))
    patient_id = None
    try:
        with get_session() as db:
            patient = patient_service.find_by_phone(db, phone)
            patient_id = patient.id if patient else None
    except Exception:
        logger.exception("❌ [Node:lookup] DB lookup failed, treating caller as new")

    return {
        "caller_type": "existing" if patient_id else "new",
        "patient_id": patient_id,
        "is_verified": False,
        "verify_attempts": 0,
        "urgency": "routine",
    }

def urgency_check(state: CallState):
    """Check EVERY message for emergencies before the LLM sees it. If the check itself fails, assume 'urgent' to be safe."""
    try:
        level = classify(_last_user_text(state))
    except Exception:
        logger.exception("❌ [Node:urgency] Rule check failed, assuming urgent")
        level = "urgent"
    highest = max(state.get("urgency", "routine"), level, key=RANK.get)
    return {"turn_level": level, "urgency": highest}

def escalate(state: CallState, config: RunnableConfig):
    """Emergency: save a callback, alert the doctor, and ask the phone layer to transfer. Always transfers, even if saving fails."""
    call_id = config["configurable"]["thread_id"]
    phone = state.get("caller_phone", "unknown")
    issue = _last_user_text(state)

    name = None
    
    try:
        if state.get("patient_id"):
            with get_session() as db:
                patient = db.get(Patient, state["patient_id"])
                name = patient.full_name if patient else None
    except Exception:
        logger.exception("❌ [Node:escalate] Could not load patient name")

    call_service.create_callback(call_id=call_id, phone=phone, name=name, issue=issue, urgency="emergency")
    notify_doctor(f"{name or 'Unknown caller'} ({phone}) said: {issue}", level="emergency")

    return {
        "messages": [AIMessage(pick_reply("emergency_transfer"))],
        "action": "transfer",    # the phone layer reads this and transfers the call
        "urgency": "emergency",
    }

class Identity(BaseModel):
    """What the LLM extracts from the caller's words while verifying."""
    full_name: str | None = Field(None, description="Full name the caller said, else null")
    date_of_birth: str | None = Field(None, description="Date of birth as YYYY-MM-DD, else null")

def verify_identity(state: CallState):
    """Ask for name + date of birth and check them against the DB. Stops after MAX_VERIFY_ATTEMPTS."""
    # Pull name / DOB out of what the caller said
    try:
        found = get_llm().with_structured_output(Identity).invoke([
            SystemMessage(
              "Extract the caller's full name and date of birth from their message. "
              "The message may be in English, Hindi or Odia. Write the name in English (Latin) letters, "
              "transliterating if needed. Use null for anything not clearly stated. Never guess."),
            HumanMessage(_last_user_text(state)),
        ])
    except Exception:
        logger.exception("❌ [Node:verify] Could not extract name/DOB")
        return {"messages": [AIMessage(pick_reply("ask_identity"))]}

    # Merge with earlier turns (name now, DOB next turn is fine)
    name = found.full_name or state.get("given_name")
    dob = found.date_of_birth or state.get("given_dob")

    if not name and not dob:
        return {"messages": [AIMessage(pick_reply("ask_identity"))]}
    if not name:
        return {"messages": [AIMessage(pick_reply("ask_name"))], "given_dob": dob}
    if not dob:
        return {"messages": [AIMessage(pick_reply("ask_dob"))], "given_name": name}

    # Compare with the record
    try:
        with get_session() as db:
            patient = db.get(Patient, state["patient_id"])
            ok = patient is not None and patient_service.verify(patient, name, dob)
            first_name = patient.full_name.split()[0] if patient else ""
    except Exception:
        logger.exception("❌ [Node:verify] DB check failed")
        return {"messages": [AIMessage(pick_reply("error"))]}

    if ok:
        logger.info("✅ [Node:verify] Patient verified")
        return {
            "is_verified": True, 
            "given_name": None, 
            "given_dob": None,
            "messages": [AIMessage(pick_reply("verified").format(first_name=first_name))]
        }

    attempts = state.get("verify_attempts", 0) + 1
    logger.warning("⚠️ [Node:verify] Verification failed (attempt %s)", attempts)
    reply = pick_reply("verify_failed") if attempts >= MAX_VERIFY_ATTEMPTS else pick_reply("verify_retry")
    return {
        "verify_attempts": attempts, 
        "given_name": None, 
        "given_dob": None,
        "messages": [AIMessage(reply)]
    }

def assistant(state: CallState):
    """Normal conversation: the LLM answers or calls a tool. On LLM failure the caller hears an approved error line."""
    try:
        llm = get_llm().bind_tools(TOOLS)
        # llm = get_agent_llm().bind_tools(TOOLS)
        # Keep the last 30 messages so long calls stay fast and cheap
        recent = trim_messages(state["messages"], strategy="last", token_counter=len, max_tokens=30, start_on="human")
        response = llm.invoke([SystemMessage(build_system_prompt(state))] + recent)
        return {"messages": [response]}
    except Exception:
        logger.exception("❌ [Node:assistant] LLM call failed")
        return {"messages": [AIMessage(pick_reply("error"))]}
    
def callback_reply(state: CallState):
    """After a callback is saved, say a fixed approved line instead of calling the LLM again (saves about 2 s)."""
    saved = True
    reason = "other"
    for message in reversed(state["messages"]):
        if isinstance(message, ToolMessage):
            saved = "could not" not in str(message.content).lower()
        elif isinstance(message, AIMessage) and message.tool_calls:
            reason = (message.tool_calls[0].get("args") or {}).get("reason", "other")
            break

    if not saved:
        key = "callback_failed"
    elif reason == "medical_question":
        key = "medical_advice_decline"    # the approved refusal line also says the question was noted
    else:
        key = "callback_saved"
    return {"messages": [AIMessage(pick_reply(key))]}