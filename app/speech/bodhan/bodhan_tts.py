"""
app/speech/bodhan_tts.py
────────────────────────
Text-to-speech with Bodhan `indic-speak`.
Replies are split into short pieces (Bodhan asks for 1-2 sentences per request); pieces are made
in parallel and played in order. Fixed lines (greetings etc.) are cached in memory, and the
greetings are pre-generated at startup so the first words of a call play instantly.
Language (en / hi / or) is detected from the script of the text.
"""

import asyncio
import json
import logging
import re
import textwrap
from functools import lru_cache
import time

from app.agent.loader import iter_variants, load_replies
from app.config import settings
from app.speech.audio_utils import wav_to_ulaw_8k
from app.speech.bodhan.client import describe_error, post_with_retry

logger = logging.getLogger("voice.tts")

# The 45 voice names Bodhan lists (case-sensitive). Used only to warn about typos at startup.
KNOWN_VOICES = {
    "Prastuti", "Ankur", "Ishita", "Sourav", "Gwrbw", "Sansuma", "Preeti", "Sham", "Dhara", "Parth",
    "Kavya", "Suhani", "Amit", "Deepika", "Adarsh", "Anjali", "Sandeep", "Zoon", "Ishfaq", "Vaidehi",
    "Madhukar", "Lakshmi", "Kiran", "Thoibi", "Chaoba", "Anagha", "Chinmay", "Srijana", "Sagar",
    "Itishree", "Akash", "Kaur", "Manpreet", "Bharati", "Aryaman", "Phulmani", "Sibu", "Moomal",
    "Rano", "Anitha", "Arun", "Sravani", "Vamsi", "Saba", "Zaid",
}

# Lines made once at startup so they play instantly during calls
# PREWARM_KEYS = (
#     "greeting", 
#     "filler", 
#     "emergency_transfer", 
#     "ask_identity",
#     "not_understood", 
#     "callback_saved", 
#     "medical_advice_decline"
# )

PREWARM_KEYS = (
    "greeting",
    "filler",
    "callback_saved",
    "emergency_transfer",
)

_ODIA = re.compile(r"[\u0B00-\u0B7F]")
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")
_ABBREVIATIONS = ("Dr.", "Mr.", "Mrs.", "Ms.", "Prof.", "St.")  # never split after these

_SEMAPHORE = asyncio.Semaphore(1)               # at most 4 TTS requests at the same time
_REQUEST_INTERVAL_SECONDS = 1.0
_LAST_REQUEST_AT = 0.0
_CACHE: dict[tuple, bytes] = {}                 # (voice, style, text) -> audio, fixed lines only


def is_known_voice(name: str) -> bool:
    """True if `name` is exactly one of Bodhan's voice names."""
    return name in KNOWN_VOICES


def detect_language(text: str) -> str:
    """Return 'or' for Odia script, 'hi' for Devanagari, otherwise 'en'."""
    odia = len(_ODIA.findall(text))
    hindi = len(_DEVANAGARI.findall(text))
    if odia and odia >= hindi:
        return "or"
    return "hi" if hindi else "en"


def split_sentences(text: str, max_chars: int = 400) -> list[str]:
    """Cut text into pieces. The first sentence stays on its own (so it plays as early as possible),
    later short sentences are merged, and 'Dr.' is never split."""
    protected = text.strip()
    for abbreviation in _ABBREVIATIONS:
        protected = protected.replace(abbreviation, abbreviation.replace(".", "§"))
    sentences = [s.replace("§", ".").strip() for s in _SENTENCE_END.split(protected) if s.strip()]

    if len(sentences) > 1 and len(sentences[0]) < 12:      # "Okay." alone sounds choppy
        sentences[0:2] = [f"{sentences[0]} {sentences[1]}"]

    # pieces: list[str] = sentences[:1]
    # for sentence in sentences[1:]:
    #     if len(sentence) < 40 and len(pieces) > 1 and len(pieces[-1]) + len(sentence) < max_chars:
    #         pieces[-1] = f"{pieces[-1]} {sentence}"         # short, so join it to the previous later piece
    #     else:
    #         pieces.append(sentence)
    
    pieces: list[str] = sentences[:1]
    if len(sentences) > 1:
        pieces.append(" ".join(sentences[1:]))    # everything after the first sentence is ONE request

    result: list[str] = []
    for piece in pieces:
        result.extend(textwrap.wrap(piece, max_chars) or [piece])
    return result or [text.strip()]


@lru_cache
def _fixed_pieces() -> frozenset[str]:
    """Every piece of every fixed line in fixed_replies.json (only these are cached, never patient-specific text)."""
    pieces: set[str] = set()
    for value in load_replies().values():
        for text in iter_variants(value):
            pieces.update(split_sentences(text))
    return frozenset(pieces)

async def synthesize_ulaw(text: str, lang: str | None = None) -> bytes:
    """Convert text to 8 kHz mu-law audio, using cache and controlled API requests."""
    global _LAST_REQUEST_AT

    key = (settings.BODHAN_VOICE, settings.BODHAN_TTS_STYLE, lang, text)

    # Fast cache check
    if key in _CACHE:
        return _CACHE[key]

    instructions = {"lang": lang or detect_language(text)}
    if settings.BODHAN_TTS_STYLE:
        instructions["style"] = settings.BODHAN_TTS_STYLE

    async with _SEMAPHORE:
        # Check again because another task may have generated this audio
        if key in _CACHE:
            return _CACHE[key]

        # Space out requests to Bodhan
        elapsed = time.monotonic() - _LAST_REQUEST_AT
        wait_time = _REQUEST_INTERVAL_SECONDS - elapsed

        if wait_time > 0:
            await asyncio.sleep(wait_time)

        _LAST_REQUEST_AT = time.monotonic()

        response = await post_with_retry(
            "/audio/speech",
            headers={"Authorization": f"Bearer {settings.tts_api_key}"},
            json={
                "model": "indic-speak",
                "input": text,
                "voice": settings.BODHAN_VOICE,
                "instructions": json.dumps(instructions),
            },
        )

        if response.status_code != 200:
            logger.error(
                "❌ [TTS] Bodhan HTTP %s - %s",
                response.status_code,
                describe_error(response),
            )
            raise RuntimeError(f"Bodhan TTS failed ({response.status_code})")

        ulaw = wav_to_ulaw_8k(response.content)

        if text in _fixed_pieces():
            _CACHE[key] = ulaw

        return ulaw


async def prewarm_fixed_lines() -> None:
    """Pre-generate only essential fixed phrases, one at a time."""
    try:
        replies = load_replies()
        pieces = []

        # for key in PREWARM_KEYS:
        #     value = replies.get(key, [])
        #     for text in (value if isinstance(value, list) else [value]):
        #         pieces.extend(split_sentences(text))

        for key in PREWARM_KEYS:
            for text in iter_variants(replies.get(key, [])):
                pieces.extend(split_sentences(text))
                
        unique = list(dict.fromkeys(pieces))
        successful = 0

        for piece in unique:
            try:
                await synthesize_ulaw(piece)
                successful += 1
            except Exception:
                logger.warning("⚠️ [TTS] Could not prewarm one fixed phrase")

        logger.info(
            "✅ [TTS] Prewarmed %s/%s essential phrases",
            successful,
            len(unique),
        )

    except Exception:
        logger.exception("⚠️ [TTS] Pre-generation failed")