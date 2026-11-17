"""
app/agent/state.py
──────────────────
The memory of ONE call. LangGraph saves this after every turn (per call_id),
so the bot remembers who is calling, whether they're verified, and how urgent it is.
"""

from langgraph.graph import MessagesState # type: ignore


class CallState(MessagesState):          # MessagesState already holds `messages`
    caller_phone: str
    caller_type: str                     # "existing" | "new"
    patient_id: int | None
    is_verified: bool
    verify_attempts: int
    given_name: str | None               # name the caller said while verifying
    given_dob: str | None                # DOB (YYYY-MM-DD) the caller said
    urgency: str                         # highest level so far: routine | urgent | emergency
    turn_level: str                      # level of the CURRENT message only
    action: str | None                   # "transfer" means the phone layer must transfer the call