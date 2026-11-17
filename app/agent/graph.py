"""
app/agent/graph.py
──────────────────
Connects the nodes into the call flow:

START -> lookup_patient -> urgency_check -+-> escalate ---------> END    (emergency)
                                          +-> verify_identity --> END    (existing, not verified yet)
                                          +-> assistant <-> tools -> END (everything else)

Each caller message runs the graph once. The checkpointer saves the state
per call_id, so the next message continues where this one stopped.
"""

from langgraph.graph import END, START, StateGraph # type: ignore
from langgraph.prebuilt import ToolNode, tools_condition # type: ignore
from app.agent.state import CallState
from app.agent.tools import TOOLS
from app.agent.nodes import (
    MAX_VERIFY_ATTEMPTS, 
    assistant, 
    callback_reply, 
    escalate,
    lookup_patient, 
    urgency_check, 
    verify_identity
)


def route_after_urgency(state: CallState) -> str:
    """Pick the next node: emergency first, then verification for unverified existing patients, else the assistant."""
    if state.get("turn_level") == "emergency":
        return "escalate"
    if (state.get("caller_type") == "existing"
            and not state.get("is_verified")
            and state.get("verify_attempts", 0) < MAX_VERIFY_ATTEMPTS):
        return "verify_identity"
    return "assistant"


def build_graph(checkpointer):
    """Wire all nodes together and compile the graph with the given checkpointer."""
    builder = StateGraph(CallState)

    builder.add_node("lookup_patient", lookup_patient)
    builder.add_node("urgency_check", urgency_check)
    builder.add_node("escalate", escalate)
    builder.add_node("verify_identity", verify_identity)
    builder.add_node("assistant", assistant)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.add_node("callback_reply", callback_reply)

    builder.add_edge(START, "lookup_patient")
    builder.add_edge("lookup_patient", "urgency_check")
    builder.add_conditional_edges("urgency_check", route_after_urgency,
                                  ["escalate", "verify_identity", "assistant"])
    builder.add_edge("escalate", END)
    builder.add_edge("verify_identity", END)
    builder.add_conditional_edges("assistant", tools_condition)   # tool call -> tools, else END
    builder.add_edge("tools", "callback_reply")
    builder.add_edge("callback_reply", END)

    return builder.compile(checkpointer=checkpointer)