"""
app/agent/loader.py
───────────────────
Loads the doctor-approved JSON files from /data once and keeps them in memory.
To change what the bot says, edit the JSON and restart. No code changes.
A missing or broken file raises a clear error at startup.
"""

import json
import random
import contextvars
from typing import Iterator
from functools import lru_cache
from pathlib import Path

from app.config import settings

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# The caller's language for the current turn. Set by the call session, read by pick_reply and the prompt.
_REPLY_LANGUAGE: contextvars.ContextVar[str] = contextvars.ContextVar("reply_language", default="")

# Replies the code uses by key. If one is missing we want to know at startup, not mid-call.
REQUIRED_REPLIES = (
    "greeting", 
    "ask_identity", 
    "ask_name", 
    "ask_dob", 
    "verified", 
    "verify_retry",
    "verify_failed",
    "not_understood",
    "medical_advice_decline", 
    "emergency_transfer", 
    "transfer_failed", 
    "error",
    "filler",
    "callback_saved",
    "callback_failed",
)

def set_reply_language(lang: str) -> None:
    """Remember the caller's language for this turn ('en', 'hi' or 'or')."""
    _REPLY_LANGUAGE.set(lang)

def current_language() -> str:
    """The caller's language for this turn; falls back to DEFAULT_LANGUAGE."""
    return _REPLY_LANGUAGE.get() or settings.DEFAULT_LANGUAGE

def iter_variants(value) -> Iterator[str]:
    """Every text variant inside a fixed reply: a string, a list, or a dict of language -> those."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        yield from (v for v in value if isinstance(v, str))
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_variants(item)

def _read(name: str) -> dict:
    """Read one JSON file from /data. Raises RuntimeError with a clear message if missing or invalid."""
    path = DATA_DIR / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise RuntimeError(f"Data file missing: {path}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Data file '{name}' is not valid JSON: {e}") from e


@lru_cache
def load_clinic() -> dict:
    """Clinic info: address, timings, fees, procedures, FAQs."""
    return _read("clinic_info.json")


@lru_cache
def load_rules() -> dict:
    """Emergency and urgent phrases approved by the doctor."""
    return _read("emergency_rules.json")


@lru_cache
def load_replies() -> dict:
    """Fixed lines the bot says. Each key is a single string or a list of variants."""
    return _read("fixed_replies.json")


def pick_reply(key: str, lang: str | None = None) -> str:
    """Return a fixed reply in the caller's language (English if that language is missing), random variant."""
    value = load_replies()[key]
    if isinstance(value, dict):
        value = value.get(lang or current_language()) or value.get("en") or next(iter(value.values()))
    return random.choice(value) if isinstance(value, list) else value


def validate_data_files() -> None:
    """Load everything once at startup and check the required reply keys exist and are not empty."""
    load_clinic()
    load_rules()
    replies = load_replies()
    missing = [key for key in REQUIRED_REPLIES if not replies.get(key)]
    if missing:
        raise RuntimeError(f"fixed_replies.json is missing or has empty keys: {missing}")