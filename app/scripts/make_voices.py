"""
app/scripts/make_voices.py
──────────────────────────
Save Bodhan's speech as WAV files you can play. No transcripts, no agent, just audio.
Files go into the folder  voice_samples/  (it opens when finished):
  <name>_hd.wav     Bodhan's original quality
  <name>_phone.wav  what a caller hears on the phone (8 kHz)

Run:
  python -m app.scripts.make_voices            # uses BODHAN_VOICE from .env
  python -m app.scripts.make_voices Suhani     # try another voice
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from app.config import settings
from app.speech.audio_utils import _read_wav, pcm_to_wav, ulaw_to_pcm, wav_to_ulaw_8k
from app.speech.client import close_http_client, describe_error, post_with_retry

OUT = Path("voice_samples")

SAMPLES = [
    ("english", "en", "Hello, my name is Subham and my knee has been paining for two days after an accident."),
    ("hindi", "hi", "नमस्ते, मेरा नाम शुभम है और दो दिन से मेरे घुटने में बहुत दर्द हो रहा है।"),
    ("odia", "or", "ନମସ୍କାର, ମୋ ନାମ ଶୁଭମ୍। ଦୁଇ ଦିନ ହେଲା ମୋ ଆଣ୍ଠୁରେ ବହୁତ ଯନ୍ତ୍ରଣା ହେଉଛି।"),
    ("hindi_english", "hi", "Hello, मेरा नाम Subham है और accident के बाद से मेरे हाथ में pain हो रहा है।"),
    ("odia_english", "or", "ନମସ୍କାର, ମୋ ନାମ Subham। Accident ପରଠାରୁ ମୋ ହାତରେ ବହୁତ pain ହେଉଛି।"),
    ("hindi_odia", "hi", "नमस्ते, मेरा नाम शुभम है। ମୋ ହାତରେ ବହୁତ ଯନ୍ତ୍ରଣା ହେଉଛି।"),
    ("all_three", "hi", "Hi, my name is Subham. मेरे हाथ में दर्द है। ମୋତେ କଣ କରିବାକୁ ହେବ?"),
    ("romanized_mix", "en", "Hi, my name is Subham, mere hand mein accident ke baad pain ho raha hai, so kana karibi?"),
]


async def make(name: str, lang: str, text: str, voice: str) -> None:
    """Ask Bodhan to speak `text`, then save the original and the phone-quality WAV files."""
    instructions = {"lang": lang}
    if settings.BODHAN_TTS_STYLE:
        instructions["style"] = settings.BODHAN_TTS_STYLE
    response = await post_with_retry(
        "/audio/speech",
        headers={"Authorization": f"Bearer {settings.tts_api_key}"},
        json={"model": "indic-speak", "input": text, "voice": voice,
              "instructions": json.dumps(instructions)},
    )
    if response.status_code != 200:
        print(f"❌ {name}: {describe_error(response)}")
        return
    pcm, rate, _ = _read_wav(response.content)
    (OUT / f"{name}_hd.wav").write_bytes(pcm_to_wav(pcm, rate))
    phone = wav_to_ulaw_8k(response.content)
    (OUT / f"{name}_phone.wav").write_bytes(pcm_to_wav(ulaw_to_pcm(phone), 8000))
    print(f"✅ {name}")


async def main() -> None:
    """Make every sample, then open the output folder."""
    voice = sys.argv[1] if len(sys.argv) > 1 else settings.BODHAN_VOICE
    OUT.mkdir(exist_ok=True)
    print(f"Voice: {voice}")
    for name, lang, text in SAMPLES:
        try:
            await make(name, lang, text, voice)
        except Exception as e:
            print(f"❌ {name}: {e}")
    await close_http_client()
    try:
        os.startfile(OUT)  # Windows: opens the folder
    except Exception:
        print(f"Files are in: {OUT.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())