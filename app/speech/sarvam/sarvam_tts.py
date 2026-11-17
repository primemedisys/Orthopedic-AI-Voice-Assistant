"""
app/speech/sarvam/sarvam_tts.py
───────────────────────────────
Sarvam Bulbul v3 text-to-speech (REST). Asks for 8 kHz audio so it matches the phone line,
with number/date preprocessing on, then converts to mu-law. Streaming comes later.
"""

import base64
import logging

from app.config import settings
from app.speech.audio_utils import wav_to_ulaw_8k
from app.speech.base import QuotaError, SpeechError
from app.speech.sarvam.client import describe_error, post

logger = logging.getLogger("voice.sarvam.tts")

LANGUAGE_CODES = {"or": "od-IN", "hi": "hi-IN", "en": "en-IN"}


async def synthesize_ulaw(text: str, lang: str = "or") -> bytes:
    """Speak `text` and return 8 kHz mu-law audio. Raises QuotaError (limits/credits) or SpeechError."""
    payload = {
        "text": text,
        "target_language_code": LANGUAGE_CODES.get(lang, "en-IN"),
        "speaker": settings.SARVAM_TTS_SPEAKER,
        "model": settings.SARVAM_TTS_MODEL,
        "pace": settings.SARVAM_TTS_PACE,
        "temperature": settings.SARVAM_TTS_TEMPERATURE,
        "speech_sample_rate": 8000,
        "output_audio_codec": "wav",
        "enable_preprocessing": True,      # reads numbers and dates naturally
    }
    response = await post("/text-to-speech", json=payload)

    if response.status_code == 429:
        raise QuotaError(f"Sarvam TTS limit: {describe_error(response)}")
    if response.status_code != 200:
        logger.error("❌ [TTS] Sarvam HTTP %s - %s", response.status_code, describe_error(response))
        raise SpeechError(f"Sarvam TTS failed ({response.status_code})")

    audios = response.json().get("audios") or []
    if not audios:
        raise SpeechError("Sarvam TTS returned no audio")
    return b"".join(wav_to_ulaw_8k(base64.b64decode(chunk)) for chunk in audios)