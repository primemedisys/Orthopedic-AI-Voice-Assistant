import json
from functools import lru_cache

from app.config import get_settings


@lru_cache
def _data() -> dict:
    """Load clinic.json once and keep it in memory."""
    with open(get_settings().clinic_data_path, encoding="utf-8") as f:
        return json.load(f)


def message(key: str) -> str:
    """Get a spoken message by name, e.g. message('greeting')."""
    return _data()["messages"][key]


def faq_catalog() -> list[dict]:
    """List FAQ ids and topics (no answers) so the LLM can pick the right one."""
    return [{"id": f["id"], "topic": f["topic"]} for f in _data()["faqs"]]


def faq_answer(faq_id: str | None) -> str | None:
    """Return the approved answer for a FAQ id, or None if not found."""
    for faq in _data()["faqs"]:
        if faq["id"] == faq_id:
            return faq["answer"]
    return None


def is_urgent(text: str) -> bool:
    """True if the caller's words contain any approved urgent keyword."""
    text = text.lower()
    return any(keyword in text for keyword in _data()["urgent_keywords"])