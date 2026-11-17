"""
app/scripts/simulate_call.py
────────────────────────────
Pretend to be Plivo: connect to your RUNNING server and "speak" WAV files as the caller.
No Plivo account or phone needed. The bot's spoken replies are saved as WAV files so you can listen.

Run (server must be running):
  python -m app.scripts.simulate_call +919999900001 caller_1.wav caller_2.wav caller_3.wav
"""

import asyncio
import audioop
import base64
import json
import sys
import wave
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import websockets

from app.speech.audio_utils import pcm_to_wav, ulaw_to_pcm

WS_URL = "ws://localhost:8000/voice/plivo/stream"
FRAME = 160  # 20 ms of 8 kHz mu-law


def load_frames(path: str) -> list[bytes]:
    """Read a 16-bit WAV, convert to 8 kHz mu-law, add 2 s of silence, and cut it into 20 ms frames."""
    with wave.open(path, "rb") as wav:
        if wav.getsampwidth() != 2:
            raise SystemExit(f"{path}: please use a 16-bit WAV file")
        pcm, rate, channels = wav.readframes(wav.getnframes()), wav.getframerate(), wav.getnchannels()
    if channels == 2:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
    if rate != 8000:
        pcm, _ = audioop.ratecv(pcm, 2, 1, rate, 8000, None)
    ulaw = audioop.lin2ulaw(pcm, 2) + b"\xff" * 16000  # 0xFF = mu-law silence
    return [ulaw[i:i + FRAME] for i in range(0, len(ulaw), FRAME)]


def save_wav(name: str, ulaw: bytes) -> None:
    """Save the bot's audio as a playable WAV."""
    Path(name).write_bytes(pcm_to_wav(ulaw_to_pcm(ulaw), 8000))


async def main(phone: str, files: list[str]) -> None:
    """Connect, send the 'start' event, then speak each file and wait for the bot's reply."""
    call_id = f"sim-{uuid4().hex[:8]}"
    audio = bytearray()
    finished: asyncio.Queue = asyncio.Queue()

    async with websockets.connect(WS_URL) as ws:

        async def reader() -> None:
            """Collect the bot's audio; on each checkpoint confirm playback like Plivo does."""
            async for raw in ws:
                msg = json.loads(raw)
                if msg["event"] == "playAudio":
                    audio.extend(base64.b64decode(msg["media"]["payload"]))
                elif msg["event"] == "checkpoint":
                    await ws.send(json.dumps({"event": "playedStream", "sequenceNumber": 0,
                                              "streamId": msg["streamId"], "name": msg["name"]}))
                    await finished.put(bytes(audio))
                    audio.clear()

        reader_task = asyncio.create_task(reader())

        await ws.send(json.dumps({
            "event": "start", "sequenceNumber": 1,
            "start": {"callId": call_id, "streamId": "sim-stream", "accountId": "SIM", "tracks": ["inbound"],
                      "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000}},
            "extra_headers": f"callerPhone={quote(phone, safe='')}",
        }))
        save_wav("sim_greeting.wav", await asyncio.wait_for(finished.get(), 60))
        print("🔊 Greeting saved: sim_greeting.wav")

        for n, path in enumerate(files, 1):
            print(f"🎤 Speaking {path}")
            for frame in load_frames(path):
                await ws.send(json.dumps({
                    "event": "media", "sequenceNumber": n, "streamId": "sim-stream",
                    "media": {"track": "inbound", "timestamp": "0", "chunk": 0,
                              "payload": base64.b64encode(frame).decode()},
                    "extra_headers": "",
                }))
                await asyncio.sleep(0.002)
            reply = await asyncio.wait_for(finished.get(), 120)
            save_wav(f"sim_reply_{n}.wav", reply)
            print(f"🔊 Bot replied: sim_reply_{n}.wav ({len(reply) / 8000:.1f} s)")

        reader_task.cancel()
    print("👋 Call ended. Check the server terminal for the summary and doctor alert.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit("Usage: python -m app.scripts.simulate_call +919999900001 caller_1.wav [caller_2.wav ...]")
    try:
        asyncio.run(main(sys.argv[1], sys.argv[2:]))
    except Exception as e:
        print(f"❌ Simulation failed: {e!r}")