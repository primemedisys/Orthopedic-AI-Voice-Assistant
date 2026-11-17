"""
app/speech/audio_utils.py
─────────────────────────
Audio helpers for the phone line:
  - convert between phone audio (8-bit mu-law) and normal 16-bit PCM
  - wrap PCM as WAV for Bodhan
  - VoiceActivityDetector: finds where the caller starts and stops talking
Uses `audioop` (built into Python 3.11; on 3.13+ the audioop-lts package provides it).
"""

import audioop
import io
import struct
import wave
from collections import deque

BYTES_PER_MS = 16  # 8 kHz * 2 bytes per sample / 1000


def ulaw_to_pcm(data: bytes) -> bytes:
    """Convert 8-bit mu-law phone audio into 16-bit PCM."""
    return audioop.ulaw2lin(data, 2)


def pcm_to_wav(pcm: bytes, rate: int) -> bytes:
    """Wrap raw 16-bit mono PCM in a WAV container."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


def utterance_to_wav(pcm_8k: bytes) -> bytes:
    """Prepare one caller sentence for Bodhan STT: upsample 8 kHz to 16 kHz and make a WAV."""
    pcm_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)
    return pcm_to_wav(pcm_16k, 16000)


def _read_wav(data: bytes) -> tuple[bytes, int, int]:
    """Return (16-bit PCM, sample rate, channels) from WAV bytes, even if the header size fields are placeholders."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            if wav.getsampwidth() != 2:
                raise ValueError("Expected 16-bit PCM WAV from Bodhan")
            pcm = wav.readframes(wav.getnframes())
            if pcm:
                return pcm, wav.getframerate(), wav.getnchannels()
    except wave.Error:
        pass  # streaming-style header: parse it by hand below

    index = data.find(b"data")
    if data[:4] != b"RIFF" or index < 0:
        raise ValueError("Bodhan did not return a WAV file")
    channels = struct.unpack("<H", data[22:24])[0]
    rate = struct.unpack("<I", data[24:28])[0]
    pcm = data[index + 8:]
    return pcm[: len(pcm) // 2 * 2], rate, channels


def wav_to_ulaw_8k(data: bytes) -> bytes:
    """Convert Bodhan's WAV (24 kHz) into 8 kHz mu-law, the format the phone line plays."""
    pcm, rate, channels = _read_wav(data)
    if channels == 2:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
    if rate != 8000:
        pcm, _ = audioop.ratecv(pcm, 2, 1, rate, 8000, None)
    return audioop.lin2ulaw(pcm, 2)


class VoiceActivityDetector:
    """Energy-based detector: feed it 20 ms frames, it returns a finished sentence when the caller stops."""

    def __init__(
        self, 
        rms_threshold: int, 
        end_silence_ms: int, 
        min_speech_ms: int,
        max_utterance_ms: int, 
        preroll_ms: int = 200
        ) -> None:
        
        self.rms_threshold = rms_threshold
        self.end_silence_ms = end_silence_ms
        self.min_speech_ms = min_speech_ms
        self.max_utterance_ms = max_utterance_ms
        self._preroll: deque[bytes] = deque(maxlen=max(1, preroll_ms // 20))  # keeps the first syllable
        self.reset()

    def reset(self) -> None:
        """Forget the current sentence and wait for new speech."""
        self.in_speech = False
        self.speech_ms = 0
        self._silence_ms = 0
        self._buffer = bytearray()
        self._preroll.clear()

    def feed(self, frame: bytes) -> bytes | None:
        """Add one PCM frame. Returns the finished utterance (PCM 8 kHz) or None."""
        ms = len(frame) // BYTES_PER_MS
        voiced = audioop.rms(frame, 2) >= self.rms_threshold

        if not self.in_speech:
            self._preroll.append(frame)
            if voiced:
                self.in_speech = True
                self.speech_ms = ms
                self._silence_ms = 0
                self._buffer = bytearray(b"".join(self._preroll))
                self._preroll.clear()
            return None

        self._buffer += frame
        if voiced:
            self.speech_ms += ms
            self._silence_ms = 0
        else:
            self._silence_ms += ms

        too_long = len(self._buffer) // BYTES_PER_MS >= self.max_utterance_ms
        if self._silence_ms >= self.end_silence_ms or too_long:
            utterance = bytes(self._buffer)
            long_enough = self.speech_ms >= self.min_speech_ms
            self.reset()
            return utterance if long_enough else None  # tiny sounds (clicks, coughs) are dropped
        return None