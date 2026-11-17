"""
app/speech/text_utils.py
────────────────────────
Text helpers shared by every speech provider:
  - detect_language: which language a piece of text is written in (by script)
  - split_sentences: first sentence alone (plays early), everything else as ONE piece
  - detect_caller_language: language of a transcript, for providers that do not report it
"""

import re
import textwrap

_ODIA = re.compile(r"[\u0B00-\u0B7F]")
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_LATIN = re.compile(r"[A-Za-z]")
_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")
_ABBREVIATIONS = ("Dr.", "Mr.", "Mrs.", "Ms.", "Prof.", "St.")  # never split after these


def detect_language(text: str) -> str:
    """'or' for Odia script, 'hi' for Devanagari, otherwise 'en'."""
    odia = len(_ODIA.findall(text))
    hindi = len(_DEVANAGARI.findall(text))
    if odia and odia >= hindi:
        return "or"
    return "hi" if hindi else "en"


def detect_caller_language(text: str) -> str | None:
    """Language of a transcript from its letters. None if it is too short (under 4 words)."""
    if len(text.split()) < 4:
        return None
    counts = {"or": len(_ODIA.findall(text)), "hi": len(_DEVANAGARI.findall(text)), "en": len(_LATIN.findall(text))}
    best = max(counts, key=counts.get)
    return best if counts[best] else None


def split_sentences(text: str, max_chars: int = 400) -> list[str]:
    """Cut a reply into at most two pieces: the first sentence, then everything else together ('Dr.' is never split)."""
    protected = text.strip()
    for abbreviation in _ABBREVIATIONS:
        protected = protected.replace(abbreviation, abbreviation.replace(".", "§"))
    sentences = [s.replace("§", ".").strip() for s in _SENTENCE_END.split(protected) if s.strip()]

    if len(sentences) > 1 and len(sentences[0]) < 12:      # "Okay." alone sounds choppy
        sentences[0:2] = [f"{sentences[0]} {sentences[1]}"]

    pieces = sentences[:1]
    if len(sentences) > 1:
        pieces.append(" ".join(sentences[1:]))

    result: list[str] = []
    for piece in pieces:
        result.extend(textwrap.wrap(piece, max_chars) or [piece])
    return result or [text.strip()]