"""
app/scripts/test_voices.py
──────────────────────────
Compare Bodhan voices and styles on the same sentence, exactly as a caller would hear it on the phone.
Listen to the saved WAV files and choose your favourite for BODHAN_VOICE / BODHAN_TTS_STYLE.

Run:  python -m app.scripts.test_voices
"""

import asyncio
from pathlib import Path

from app.config import settings
from app.speech.audio_utils import pcm_to_wav, ulaw_to_pcm
from app.speech.bodhan_tts import synthesize_ulaw
from app.speech.client import close_http_client

TEXT = ("Hello, thanks for calling Dr. Pushpak Samal's clinic. I'm the clinic's AI assistant. "
        "I'm sorry to hear about your knee. How long has it been hurting?")
VOICES = ["Kavya", "Suhani", "Amit", "Itishree", "Akash"]
STYLES = ["", "Customer Care"]   # "" = default style


async def main() -> None:
    """Make one WAV per voice and style, printing ✅ / ❌ for each."""
    for voice in VOICES:
        for style in STYLES:
            settings.BODHAN_VOICE, settings.BODHAN_TTS_STYLE = voice, style
            name = f"voice_{voice}_{(style or 'default').replace(' ', '_')}.wav"
            try:
                ulaw = await synthesize_ulaw(TEXT)
                Path(name).write_bytes(pcm_to_wav(ulaw_to_pcm(ulaw), 8000))
                print(f"✅ {name}")
            except Exception as e:
                print(f"❌ {voice} / {style or 'default'}: {e}")
    await close_http_client()


if __name__ == "__main__":
    asyncio.run(main())