"""
app/scripts/test_sarvam.py
──────────────────────────
Compare Sarvam voices exactly as a phone caller hears them. For each speaker and language it prints how long
the speech took and saves voice_samples/sarvam_<speaker>_<lang>.wav. The first speaker also does a speech-to-text
round trip. Speaker names are case-sensitive; an invalid one prints Sarvam's error.

Run:  python -m app.scripts.test_sarvam               # tries several speakers
      python -m app.scripts.test_sarvam ritu shubh    # only these
"""

import asyncio
import sys
import time
from pathlib import Path

# from app.config import settings
from app.config import settings
from app.speech.audio_utils import pcm_to_wav, ulaw_to_pcm, utterance_to_wav
from app.speech.sarvam import sarvam_stt, sarvam_tts
from app.speech.sarvam.client import close_client

OUT = Path("voice_notes")
# SPEAKERS = ["ritu", "priya", "neha", "kavya", "shubh", "aditya", "amit"]
SPEAKERS = ["ritu"]

SAMPLES = {
    "or": "ନମସ୍କାର, ଡାକ୍ତର ପୁଷ୍ପକ ସାମଲଙ୍କ କ୍ଲିନିକକୁ ସ୍ୱାଗତ। ଆପଣଙ୍କ ଗୋଡ଼ରେ କେତେ ଦିନ ହେଲା ଯନ୍ତ୍ରଣା ହେଉଛି?",
    "hi": "नमस्ते, डॉक्टर पुष्पक सामल के क्लिनिक में आपका स्वागत है। आपके पैर में कितने दिनों से दर्द हो रहा है?",
    "en": "Hello, welcome to Dr. Pushpak Samal's clinic. How many days has your leg been paining?",
}


async def main() -> None:
    """Make every sample, print the timing, save the WAV files."""
    OUT.mkdir(exist_ok=True)
    speakers = sys.argv[1:] or SPEAKERS
    for index, speaker in enumerate(speakers):
        settings.SARVAM_TTS_SPEAKER = speaker
        for lang, text in SAMPLES.items():
            started = time.perf_counter()
            try:
                ulaw = await sarvam_tts.synthesize_ulaw(text, lang)
            except Exception as e:
                print(f"❌ {speaker}/{lang}: {e}")
                continue
            took = time.perf_counter() - started
            path = OUT / f"sarvam_{speaker}_{lang}.wav"
            path.write_bytes(pcm_to_wav(ulaw_to_pcm(ulaw), 8000))
            print(f"✅ {speaker:<8}{lang}  {took:.1f}s to make {len(ulaw) / 8000:.1f}s of speech  -> {path}")

            if index == 0:   # one speech-to-text round trip per language
                try:
                    started = time.perf_counter()
                    heard = await sarvam_stt.transcribe(utterance_to_wav(ulaw_to_pcm(ulaw)))
                    print(f"   🎤 heard ({time.perf_counter() - started:.1f}s, language {heard.language}): {heard.text}")
                except Exception as e:
                    print(f"   ❌ speech-to-text: {e}")
    await close_client()


if __name__ == "__main__":
    asyncio.run(main())