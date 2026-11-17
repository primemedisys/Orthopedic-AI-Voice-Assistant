"""
app/telephony/base.py
─────────────────────
The contract every phone provider must follow. Routes and the call session
talk to this interface, never to Plivo or Twilio directly.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

# Audio format of the live stream: mu-law 8 kHz (both Plivo and Twilio use it).
STREAM_ENCODING = "audio/x-mulaw"
STREAM_RATE = 8000
STREAM_CONTENT_TYPE = f"{STREAM_ENCODING};rate={STREAM_RATE}"


# def is_expected_format(fmt: dict) -> bool:
#     """True if the provider's mediaFormat is mu-law at 8 kHz."""
#     return fmt.get("encoding") == STREAM_ENCODING and int(fmt.get("sampleRate") or 0) == STREAM_RATE

def is_expected_format(fmt: dict) -> bool:
    """True if the provider's mediaFormat is mu-law at 8 kHz (the exact wording differs between providers)."""
    encoding = str(fmt.get("encoding", "")).lower()
    rate = int(fmt.get("sampleRate") or 0)
    return any(word in encoding for word in ("mulaw", "ulaw", "pcmu")) and rate in (0, STREAM_RATE)

@dataclass
class StreamEvent:
    """One provider-neutral event from the live audio stream."""
    
    kind: str                 # "start" | "media" | "mark" | "dtmf" | "stop"
    call_id: str = ""
    stream_id: str = ""
    caller_phone: str = ""
    audio: bytes = b""        # mu-law audio (for "media")
    name: str = ""            # checkpoint name (for "mark")
    format_ok: bool = True    # for "start": True if the audio format is what we expect


class TelephonyAdapter(ABC):
    """Everything the app needs from a phone provider."""

    name: str  # "plivo" or "twilio", used in the webhook URL paths

    @abstractmethod
    def stream_xml(self, ws_url: str, status_url: str, caller_phone: str) -> str:
        """XML that tells the provider to open a two-way audio WebSocket."""

    @abstractmethod
    def dial_xml(self, number: str, action_url: str, timeout: int) -> str:
        """XML that rings a phone number and reports the result to action_url."""

    @abstractmethod
    def speak_hangup_xml(self, text: str) -> str:
        """XML that speaks a short line with the provider's own voice, then hangs up."""

    @abstractmethod
    def hangup_xml(self) -> str:
        """XML that ends the call."""

    @abstractmethod
    async def transfer_call(self, call_id: str, url: str) -> None:
        """Move a live call to new instructions fetched from `url`."""

    @abstractmethod
    def validate_signature(self, method: str, url: str, nonce: str,
                           signature: str, params: dict | None = None) -> bool:
        """True if the request really came from the provider."""

    @abstractmethod
    def parse_stream_event(self, msg: dict) -> StreamEvent | None:
        """Convert a provider WebSocket message into a StreamEvent (None = ignore it)."""

    @abstractmethod
    def audio_message(self, stream_id: str, ulaw: bytes) -> dict:
        """WebSocket message that plays mu-law audio to the caller."""

    @abstractmethod
    def mark_message(self, stream_id: str, name: str) -> dict:
        """WebSocket message asking the provider to tell us when playback reaches this point."""

    @abstractmethod
    def clear_message(self, stream_id: str) -> dict:
        """WebSocket message that stops any audio still queued (used when the caller interrupts)."""
    