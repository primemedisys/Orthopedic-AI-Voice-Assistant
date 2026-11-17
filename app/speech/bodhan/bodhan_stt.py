"""
app/speech/bodhan_stt.py
────────────────────────
Speech-to-text with Bodhan `indic-transcribe` (English, Hindi, Odia).
Each request must stay under 30 seconds of audio (the VAD cuts sentences at 25 s).
"""

import logging
import audioop
import re
import io
import wave

from app.config import settings
from app.speech.bodhan.client import describe_error, post_with_retry

logger = logging.getLogger("voice.stt")

_ODIA = re.compile(r"[\u0B00-\u0B7F]")
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_LATIN = re.compile(r"[A-Za-z]")

def detect_caller_language(text: str) -> str | None:
    """'or', 'hi' or 'en' from the letters of a transcript. None if it is too short (under 4 words)
    or written in another script (an STT mistake such as Bengali or Telugu letters)."""
    if len(text.split()) < 4:
        return None
    counts = {"or": len(_ODIA.findall(text)), "hi": len(_DEVANAGARI.findall(text)), "en": len(_LATIN.findall(text))}
    best = max(counts, key=counts.get)
    return best if counts[best] else None

async def transcribe(wav_bytes: bytes, language: str | None = None) -> str:
    """Send a WAV to Bodhan and return the text. Raises RuntimeError if Bodhan rejects the request."""
    data = {"model": "indic-transcribe"}
    lang = language or settings.BODHAN_STT_LANGUAGE
    if lang:  # empty means Bodhan detects the language itself
        data["language"] = lang
    
    log_audio_info(wav_bytes)

    response = await post_with_retry(
        "/audio/transcriptions",
        headers={"Authorization": f"Bearer {settings.stt_api_key}"},
        files={"file": ("utterance.wav", wav_bytes, "audio/wav")},
        data=data,
    )
    if response.status_code != 200:
        logger.error("❌ [STT] Bodhan HTTP %s - %s", response.status_code, describe_error(response))
        raise RuntimeError(f"Bodhan STT failed ({response.status_code})")
    return (response.json().get("text") or "").strip()


def log_audio_info(wav_bytes: bytes) -> None:
    """Log WAV properties before sending audio to Bodhan."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            channels = wav.getnchannels()
            sample_rate = wav.getframerate()
            sample_width = wav.getsampwidth()
            frames = wav.readframes(wav.getnframes())

            duration = wav.getnframes() / sample_rate
            rms = audioop.rms(frames, sample_width) if frames else 0

            logger.info(
                "[STT AUDIO] bytes=%d | rate=%d Hz | channels=%d | "
                "width=%d bytes | duration=%.2fs | RMS=%d",
                len(wav_bytes),
                sample_rate,
                channels,
                sample_width,
                duration,
                rms,
            )
    except Exception:
        logger.exception("[STT AUDIO] Could not inspect WAV")