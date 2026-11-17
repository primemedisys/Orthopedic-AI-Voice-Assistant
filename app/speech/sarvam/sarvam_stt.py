"""
app/speech/sarvam/sarvam_stt.py
───────────────────────────────
Sarvam Saaras speech-to-text (REST, up to 30 s per request). Auto-detects Odia, Hindi and English
and reports which one it heard, so the call can follow the caller's language.
"""

import logging

from app.config import settings
from app.speech.base import QuotaError, SpeechError, Transcript
from app.speech.sarvam.client import describe_error, post

logger = logging.getLogger("voice.sarvam.stt")

CODE_TO_LANGUAGE = {"od-IN": "or", "hi-IN": "hi", "en-IN": "en"}


async def transcribe(wav_bytes: bytes) -> Transcript:
    """Convert a 16 kHz WAV to text. Raises QuotaError (limits/credits) or SpeechError."""
    data = {"model": settings.SARVAM_STT_MODEL}
    if settings.SARVAM_STT_MODE:
        data["mode"] = settings.SARVAM_STT_MODE
    if settings.SARVAM_STT_LANGUAGE:
        data["language_code"] = settings.SARVAM_STT_LANGUAGE

    response = await post("/speech-to-text",files={"file": ("utterance.wav", wav_bytes, "audio/wav")}, data=data)
    if response.status_code == 429:
        raise QuotaError(f"Sarvam STT limit: {describe_error(response)}")
    if response.status_code != 200:
        logger.error("❌ [STT] Sarvam HTTP %s - %s", response.status_code, describe_error(response))
        raise SpeechError(f"Sarvam STT failed ({response.status_code})")

    body = response.json()
    return Transcript(
        text=(body.get("transcript") or "").strip(),
        language=CODE_TO_LANGUAGE.get(body.get("language_code") or "")
    )