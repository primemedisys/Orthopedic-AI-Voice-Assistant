"""
app/agent/rules.py
──────────────────
Emergency detection in plain Python with NO LLM, so it is predictable and testable.
Phrases come from data/emergency_rules.json (approved by the doctor).
"""

import re

from app.agent.loader import load_rules


def _norm(text: str) -> str:
    """Lowercase, drop apostrophes ("can't" -> "cant"), and strip punctuation."""
    text = text.lower().replace("'", "").replace("’", "")
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def classify(text: str) -> str:
    """Return 'emergency', 'urgent' or 'routine' for one caller message (emergency is checked first)."""
    cleaned = _norm(text)
    rules = load_rules()
    for level in ("emergency", "urgent"):
        for phrase in rules.get(level, []):
            # \b = whole words only, so "numb" does not match "number"
            if re.search(rf"\b{re.escape(_norm(phrase))}\b", cleaned):
                return level
    return "routine"