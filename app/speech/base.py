"""
app/speech/base.py
──────────────────
Small shared types for every speech provider (Sarvam, Bodhan).
"""

from dataclasses import dataclass


class SpeechError(RuntimeError):
    """A speech provider rejected or failed a request."""


class QuotaError(SpeechError):
    """The provider refused because of a rate limit, budget or credits (prewarm stops on this)."""


@dataclass
class Transcript:
    """Speech-to-text result."""

    text: str
    language: str | None = None     # 'or' | 'hi' | 'en' when the provider reports it, else None