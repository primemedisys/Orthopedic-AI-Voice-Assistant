"""
app/agent/tools.py
──────────────────
Actions the LLM can take. The docstring tells the LLM when to use each tool.
`config` is injected automatically and is NOT shown to the LLM
(it carries the call_id and the caller's phone).
"""

from typing import Literal

from langchain_core.runnables import RunnableConfig # type: ignore
from langchain_core.tools import tool # type: ignore

from app.services import call_service


@tool
def request_callback(
    name: str,
    issue: str,
    urgency: Literal["routine", "urgent"],
    config: RunnableConfig,
    date_of_birth: str = "",
) -> str:
    """Save a callback request for the doctor's team. Choose `reason` like this:
    - new_patient: a new caller, once you have their name, date of birth and issue;
    - urgent_problem: the caller described an urgent problem;
    - medical_question: the caller asked about surgery, medicines or a diagnosis (you cannot answer);
    - other: any other question you cannot answer from the approved information.
    `issue` is a short description of the caller's problem or question."""
    cfg = config["configurable"]
    saved = call_service.create_callback(
        call_id=cfg["thread_id"],
        phone=cfg.get("caller_phone", "unknown"),
        name=name,
        issue=issue,
        urgency=urgency,
        date_of_birth=date_of_birth or None,
    )
    if not saved:
        return "The callback could NOT be saved."
    return "Callback saved."


TOOLS = [request_callback]