# """
# app/speech/factory.py
# ─────────────────────
# The rest of the app calls ONLY this file for speech:
#   transcribe(wav)              -> Transcript(text, language)
#   synthesize_ulaw(text, lang)  -> 8 kHz mu-law audio, with the disk cache for fixed lines
#   prewarm_fixed_lines()        -> makes the common spoken lines once, in Odia, Hindi, English order
# STT_PROVIDER / TTS_PROVIDER in .env choose Sarvam or Bodhan.
# """

# import asyncio
# import logging

# from app.agent.loader import iter_variants, load_replies
# from app.config import settings
# from app.speech import cache
# from app.speech.base import QuotaError, SpeechError, Transcript
# from app.speech.text_utils import detect_language, split_sentences

# logger = logging.getLogger("voice.speech")

# # Lines made once at startup so they play instantly during calls
# PREWARM_KEYS = (
#     "greeting", 
#     "filler", 
#     "emergency_transfer", 
#     "callback_saved", 
#     "not_understood", 
#     "ask_identity"
# )

# # semaphore for limiting concurrent TTS requests (Sarvam or Bodhan)
# _semaphore: asyncio.Semaphore | None = None

# def _limit() -> asyncio.Semaphore:
#     """At most TTS_CONCURRENCY speech requests at the same time."""
#     global _semaphore
#     if _semaphore is None:
#         _semaphore = asyncio.Semaphore(settings.TTS_CONCURRENCY)
#     return _semaphore


# def _voice_id() -> str:
#     """Everything that changes the sound, used in the cache file name."""
#     if settings.TTS_PROVIDER.lower() == "sarvam":
#         return f"{settings.SARVAM_TTS_MODEL}:{settings.SARVAM_TTS_SPEAKER}:{settings.SARVAM_TTS_PACE}"
#     return f"{settings.BODHAN_VOICE}:{settings.BODHAN_TTS_STYLE}"


# def missing_speech_settings() -> list[str]:
#     """Names of empty settings for the chosen speech providers."""
#     stt, tts = settings.STT_PROVIDER.lower(), settings.TTS_PROVIDER.lower()
#     missing: list[str] = []
#     if "sarvam" in (stt, tts) and not settings.SARVAM_API_KEY:
#         missing.append("SARVAM_API_KEY")
#     if tts == "sarvam" and not settings.SARVAM_TTS_SPEAKER:
#         missing.append("SARVAM_TTS_SPEAKER")
#     if stt == "bodhan" and not settings.stt_api_key:
#         missing.append("BODHAN_TRANSCRIBE_API_KEY")
#     if tts == "bodhan" and not (settings.tts_api_key and settings.BODHAN_VOICE):
#         missing.append("BODHAN_SPEAK_API_KEY / BODHAN_VOICE")
#     return missing


# async def transcribe(wav_bytes: bytes) -> Transcript:
#     """Speech to text with the chosen provider."""
#     if settings.STT_PROVIDER.lower() == "sarvam":
#         from app.speech.sarvam import sarvam_stt
#         return await sarvam_stt.transcribe(wav_bytes)
#     from app.speech.bodhan import bodhan_stt
#     return Transcript(text=await bodhan_stt.transcribe(wav_bytes), language=None)


# async def _make_speech(text: str, lang: str) -> bytes:
#     """Ask the chosen provider to speak (no cache)."""
#     if settings.TTS_PROVIDER.lower() == "sarvam":
#         from app.speech.sarvam import sarvam_tts
#         return await sarvam_tts.synthesize_ulaw(text, lang)
#     from app.speech.bodhan import bodhan_tts
#     return await bodhan_tts.synthesize_ulaw(text, lang)


# async def synthesize_ulaw(text: str, lang: str | None = None) -> bytes:
#     """Text to 8 kHz mu-law audio. Fixed lines come from the cache when they exist."""
#     lang = lang or detect_language(text)
#     provider, voice = settings.TTS_PROVIDER.lower(), _voice_id()
#     fixed = cache.is_fixed(text)
#     if fixed:
#         saved = cache.get(provider, voice, lang, text)
#         if saved:
#             return saved
#     async with _limit():
#         ulaw = await _make_speech(text, lang)
#     if fixed:
#         cache.put(provider, voice, lang, text, ulaw)
#     return ulaw


# def _variants_for(value, lang: str) -> list[str]:
#     """The text variants of one fixed reply for one language (plain lists count as English)."""
#     if isinstance(value, dict):
#         return list(iter_variants(value.get(lang) or []))
#     return list(iter_variants(value)) if lang == "en" else []


# async def prewarm_fixed_lines() -> None:
#     """Make the common lines once (default language first) and save them. Stops cleanly on a limit or budget error."""
#     try:
#         replies = load_replies()
#         order = [settings.DEFAULT_LANGUAGE] + [c for c in ("or", "hi", "en") if c != settings.DEFAULT_LANGUAGE]
#         todo: list[str] = []
#         for lang in order:
#             for key in PREWARM_KEYS:
#                 for text in _variants_for(replies.get(key), lang):
#                     todo.extend(split_sentences(text))
#         todo = list(dict.fromkeys(todo))

#         provider, voice = settings.TTS_PROVIDER.lower(), _voice_id()
#         made = already = failures = 0
#         for piece in todo:
#             if cache.get(provider, voice, detect_language(piece), piece):
#                 already += 1
#                 continue
#             try:
#                 await synthesize_ulaw(piece)
#                 made += 1
#                 failures = 0
#             except (QuotaError, SpeechError) as e:
#                 failures += 1
#                 logger.warning("⚠️ [TTS] Prewarm problem: %s", e)
#                 if isinstance(e, QuotaError) or failures >= 3:
#                     logger.warning("⚠️ [TTS] Prewarm stopped (budget, limit or errors). Calls still work for cached lines.")
#                     break
#         logger.info("✅ [TTS] Prewarm done: %s new, %s already saved, %s lines in total", made, already, len(todo))
#     except Exception:
#         logger.exception("⚠️ [TTS] Pre-generation failed")


# async def close_speech_clients() -> None:
#     """Close HTTP clients on shutdown."""
#     try:
#         from app.speech.sarvam.client import close_client
#         await close_client()
#     except Exception:
#         pass
#     try:
#         from app.speech.bodhan.client import close_http_client
#         await close_http_client()
#     except Exception:
#         pass


"""
app/speech/factory.py
─────────────────────
The rest of the app calls ONLY this file for speech:
  transcribe(wav)              -> Transcript(text, language)
  synthesize_ulaw(text, lang)  -> 8 kHz mu-law audio, with the disk cache for fixed lines
  prewarm_fixed_lines()        -> makes the common spoken lines once, in Odia, Hindi, English order
STT_PROVIDER / TTS_PROVIDER in .env choose Sarvam or Bodhan.
"""

import asyncio
import logging

from app.agent.loader import iter_variants, load_replies
from app.config import settings
from app.speech import cache
from app.speech.base import QuotaError, SpeechError, Transcript
from app.speech.text_utils import detect_language, split_sentences

# Speech provider imports
from app.speech.sarvam import sarvam_stt, sarvam_tts
from app.speech.sarvam.client import close_client
from app.speech.bodhan import bodhan_stt, bodhan_tts
from app.speech.bodhan.client import close_http_client


logger = logging.getLogger("voice.speech")

# Lines made once at startup so they play instantly during calls
PREWARM_KEYS = (
    "greeting",
    "filler",
    "emergency_transfer",
    "callback_saved",
    "not_understood",
    "ask_identity",
)

# semaphore for limiting concurrent TTS requests (Sarvam or Bodhan)
_semaphore: asyncio.Semaphore | None = None


def _limit() -> asyncio.Semaphore:
    """At most TTS_CONCURRENCY speech requests at the same time."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.TTS_CONCURRENCY)
    return _semaphore


def _voice_id() -> str:
    """Everything that changes the sound, used in the cache file name."""
    if settings.TTS_PROVIDER.lower() == "sarvam":
        return f"{settings.SARVAM_TTS_MODEL}:{settings.SARVAM_TTS_SPEAKER}:{settings.SARVAM_TTS_PACE}"
    return f"{settings.BODHAN_VOICE}:{settings.BODHAN_TTS_STYLE}"


def missing_speech_settings() -> list[str]:
    """Names of empty settings for the chosen speech providers."""
    stt, tts = settings.STT_PROVIDER.lower(), settings.TTS_PROVIDER.lower()
    missing: list[str] = []

    if "sarvam" in (stt, tts) and not settings.SARVAM_API_KEY:
        missing.append("SARVAM_API_KEY")

    if tts == "sarvam" and not settings.SARVAM_TTS_SPEAKER:
        missing.append("SARVAM_TTS_SPEAKER")

    if stt == "bodhan" and not settings.stt_api_key:
        missing.append("BODHAN_TRANSCRIBE_API_KEY")

    if tts == "bodhan" and not (settings.tts_api_key and settings.BODHAN_VOICE):
        missing.append("BODHAN_SPEAK_API_KEY / BODHAN_VOICE")

    return missing


async def transcribe(wav_bytes: bytes) -> Transcript:
    """Speech to text with the chosen provider."""
    if settings.STT_PROVIDER.lower() == "sarvam":
        return await sarvam_stt.transcribe(wav_bytes)
    return Transcript(
        text=await bodhan_stt.transcribe(wav_bytes),
        language=None,
    )


async def _make_speech(text: str, lang: str) -> bytes:
    """Ask the chosen provider to speak (no cache)."""
    if settings.TTS_PROVIDER.lower() == "sarvam":
        return await sarvam_tts.synthesize_ulaw(text, lang)
    return await bodhan_tts.synthesize_ulaw(text, lang)


async def synthesize_ulaw(
    text: str,
    lang: str | None = None,
) -> bytes:
    """Text to 8 kHz mu-law audio. Fixed lines come from the cache when they exist."""
    lang = lang or detect_language(text)
    provider, voice = settings.TTS_PROVIDER.lower(), _voice_id()

    fixed = cache.is_fixed(text)

    if fixed:
        saved = cache.get(provider, voice, lang, text)
        if saved:
            return saved

    async with _limit():
        ulaw = await _make_speech(text, lang)

    if fixed:
        cache.put(provider, voice, lang, text, ulaw)

    return ulaw


def _variants_for(value, lang: str) -> list[str]:
    """The text variants of one fixed reply for one language (plain lists count as English)."""
    if isinstance(value, dict):
        return list(iter_variants(value.get(lang) or []))
    return list(iter_variants(value)) if lang == "en" else []


async def prewarm_fixed_lines() -> None:
    """Make the common lines once (default language first) and save them. Stops cleanly on a limit or budget error."""
    try:
        replies = load_replies()

        order = [settings.DEFAULT_LANGUAGE] + [
            c for c in ("or", "hi", "en")
            if c != settings.DEFAULT_LANGUAGE
        ]

        todo: list[str] = []

        for lang in order:
            for key in PREWARM_KEYS:
                for text in _variants_for(replies.get(key), lang):
                    todo.extend(split_sentences(text))

        todo = list(dict.fromkeys(todo))

        provider, voice = settings.TTS_PROVIDER.lower(), _voice_id()
        made = already = failures = 0

        for piece in todo:
            if cache.get(provider, voice, detect_language(piece), piece):
                already += 1
                continue

            try:
                await synthesize_ulaw(piece)
                made += 1
                failures = 0

            except (QuotaError, SpeechError) as e:
                failures += 1
                logger.warning("⚠️ [TTS] Prewarm problem: %s", e)

                if isinstance(e, QuotaError) or failures >= 3:
                    logger.warning("⚠️ [TTS] Prewarm stopped (budget, limit or errors). Calls still work for cached lines.")
                    break

        logger.info("✅ [TTS] Prewarm done: %s new, %s already saved, %s lines in total", made, already, len(todo))
    except Exception:
        logger.exception("⚠️ [TTS] Pre-generation failed")


async def close_speech_clients() -> None:
    """Close HTTP clients on shutdown."""
    try:
        await close_client()
    except Exception:
        pass

    try:
        await close_http_client()
    except Exception:
        pass
