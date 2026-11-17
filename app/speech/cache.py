"""
app/speech/cache.py
───────────────────
Disk + memory cache for the FIXED spoken lines (greeting, filler, confirmations...).
A line is made once, saved as a file, and every later start reads it back instantly.
Patient-specific replies are never cached.
"""

import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path

from app.agent.loader import iter_variants, load_replies
from app.config import settings
from app.speech.text_utils import split_sentences

logger = logging.getLogger("voice.cache")

_MEMORY: dict[str, bytes] = {}


def _dir() -> Path:
    """Cache folder: TTS_CACHE_DIR if set, otherwise <project>/.cache/tts."""
    if settings.TTS_CACHE_DIR:
        return Path(settings.TTS_CACHE_DIR)
    return Path(__file__).resolve().parents[2] / ".cache" / "tts"


def _name(provider: str, voice: str, lang: str, text: str) -> str:
    """File name: a hash of everything that changes the sound (provider, voice, language, text)."""
    raw = json.dumps([provider, voice, lang, text], ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def get(provider: str, voice: str, lang: str, text: str) -> bytes | None:
    """Saved audio for this line, or None."""
    name = _name(provider, voice, lang, text)
    if name in _MEMORY:
        return _MEMORY[name]
    try:
        path = _dir() / f"{name}.ulaw"
        if path.exists():
            _MEMORY[name] = path.read_bytes()
            return _MEMORY[name]
    except Exception:
        logger.warning("⚠️ [Cache] Could not read a cached line")
    return None


def put(provider: str, voice: str, lang: str, text: str, ulaw: bytes) -> None:
    """Save audio for a fixed line (memory and disk)."""
    name = _name(provider, voice, lang, text)
    _MEMORY[name] = ulaw
    try:
        _dir().mkdir(parents=True, exist_ok=True)
        (_dir() / f"{name}.ulaw").write_bytes(ulaw)
    except Exception:
        logger.warning("⚠️ [Cache] Could not save a line to disk")


@lru_cache
def fixed_pieces() -> frozenset[str]:
    """Every spoken piece of every fixed line, in every language."""
    pieces: set[str] = set()
    for value in load_replies().values():
        for text in iter_variants(value):
            pieces.update(split_sentences(text))
    return frozenset(pieces)


def is_fixed(text: str) -> bool:
    """True if `text` is one of the fixed spoken pieces (safe to cache)."""
    return text in fixed_pieces()