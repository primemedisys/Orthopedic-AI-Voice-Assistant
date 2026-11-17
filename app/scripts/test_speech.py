"""
app/scripts/test_speech.py
──────────────────────────
Test Bodhan on its own, no server or phone needed.
  1) Makes speech in English, Hindi and Odia (tts_en.wav, tts_hi.wav, tts_or.wav = exactly what a caller hears)
  2) Reads that speech back with STT to check the round trip
  3) Makes caller_1/2/3.wav, used by simulate_call.py
Optional: add the path of your own 16-bit WAV recording (max 30 s) to test STT on a real voice.

Run:  python -m app.scripts.test_speech [my_recording.wav]
"""

import asyncio
import sys
from pathlib import Path

from app.speech.audio_utils import pcm_to_wav, ulaw_to_pcm, utterance_to_wav
from app.speech.bodhan_stt import transcribe
from app.speech.bodhan_tts import synthesize_ulaw
from app.speech.client import close_http_client

SAMPLES = {
    "en": "Hello, thanks for calling Dr. Pushpak Samal's clinic. How can I help you today?",
    "hi": "नमस्ते, डॉक्टर पुष्पक सामल के क्लिनिक में आपका स्वागत है। मैं आपकी कैसे मदद कर सकती हूँ?",
    "or": "ନମସ୍କାର, ଡାକ୍ତର ପୁଷ୍ପକ ସାମଲଙ୍କ କ୍ଲିନିକକୁ ସ୍ୱାଗତ। ମୁଁ ଆପଣଙ୍କୁ କିପରି ସାହାଯ୍ୟ କରିପାରିବି?",
}

CALLER_LINES = [
    "Hi, I want to ask about my knee.",
    "I am Rahul Sharma, born on twelve March nineteen ninety.",
    "I fell down and I can't walk.",
]


def save(name: str, ulaw: bytes) -> None:
    """Save 8 kHz mu-law audio as a playable WAV."""
    Path(name).write_bytes(pcm_to_wav(ulaw_to_pcm(ulaw), 8000))


async def main(recording: str | None) -> None:
    """Run all checks and print ✅ / ❌ for each."""
    for code, text in SAMPLES.items():
        try:
            ulaw = await synthesize_ulaw(text)
            save(f"tts_{code}.wav", ulaw)
            heard = await transcribe(utterance_to_wav(ulaw_to_pcm(ulaw)), language=code)
            print(f"✅ [{code}] saved tts_{code}.wav ({len(ulaw) / 8000:.1f} s)\n   said : {text}\n   heard: {heard}")
        except Exception as e:
            print(f"❌ [{code}] {e}")

    for n, line in enumerate(CALLER_LINES, 1):
        try:
            save(f"caller_{n}.wav", await synthesize_ulaw(line))
            print(f"✅ saved caller_{n}.wav")
        except Exception as e:
            print(f"❌ caller_{n}: {e}")

    if recording:
        try:
            print("✅ Your recording:", await transcribe(Path(recording).read_bytes()))
        except Exception as e:
            print(f"❌ Your recording: {e}")

    await close_http_client()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))