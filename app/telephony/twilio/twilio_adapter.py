"""
app/telephony/twilio_adapter.py
───────────────────────────────
Twilio implementation: TwiML with <Connect><Stream>, Twilio's Media Streams WebSocket messages,
live call transfer through the REST API, and webhook signature checks.
"""

import asyncio
import base64
import logging
from xml.sax.saxutils import escape, quoteattr

from twilio.request_validator import RequestValidator # type: ignore
from twilio.rest import Client # type: ignore

from app.config import settings
from app.telephony.base import StreamEvent, TelephonyAdapter, is_expected_format

logger = logging.getLogger("voice.twilio")


def _wrap(*parts: str) -> str:
    """Put TwiML elements inside <Response>."""
    return '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(parts) + "</Response>"


class TwilioAdapter(TelephonyAdapter):
    """Twilio Voice: TwiML, Media Streams, <Dial> and call update."""

    name = "twilio"

    def __init__(self) -> None:
        self._client: Client | None = None

    def _rest(self) -> Client:
        """Create the Twilio REST client on first use (so the app starts even without keys)."""
        if self._client is None:
            if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN):
                raise RuntimeError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN are not set")
            self._client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        return self._client

    # ── TwiML ─────────────────────────────────────────────────────

    def stream_xml(self, ws_url: str, status_url: str, caller_phone: str) -> str:
        """Open a two-way stream and pass the caller's number as a custom parameter."""
        return _wrap(
            "<Connect>"
            f"<Stream url={quoteattr(ws_url)} statusCallback={quoteattr(status_url)} "
            'statusCallbackMethod="POST">'
            f"<Parameter name=\"callerPhone\" value={quoteattr(caller_phone)}/>"
            "</Stream></Connect>"
        )

    def dial_xml(self, number: str, action_url: str, timeout: int) -> str:
        """Ring `number` and report the result to action_url."""
        caller_id = f" callerId={quoteattr(settings.TWILIO_NUMBER)}" if settings.TWILIO_NUMBER else ""
        return _wrap(
            f'<Dial timeout="{int(timeout)}"{caller_id} action={quoteattr(action_url)} method="POST">'
            f"<Number>{escape(number)}</Number></Dial>"
        )

    def speak_hangup_xml(self, text: str) -> str:
        """Speak `text` with Twilio's built-in voice, then hang up."""
        voice = f" voice={quoteattr(settings.TWILIO_VOICE)}" if settings.TWILIO_VOICE else ""
        lang = f" language={quoteattr(settings.SPEECH_LANGUAGE)}" if settings.SPEECH_LANGUAGE else ""
        return _wrap(f"<Say{voice}{lang}>{escape(text)}</Say><Hangup/>")

    def hangup_xml(self) -> str:
        """End the call."""
        return _wrap("<Hangup/>")

    # ── Live call control ─────────────────────────────────────────

    async def transfer_call(self, call_id: str, url: str) -> None:
        """Redirect the live call to new TwiML fetched from `url` (the SDK is blocking, so run it in a thread)."""
        client = self._rest()
        await asyncio.to_thread(lambda: client.calls(call_id).update(url=url, method="POST"))
        logger.info("✅ [Twilio] Call %s transferred", call_id)

    def validate_signature(self, method: str, url: str, nonce: str,
                           signature: str, params: dict | None = None) -> bool:
        """Check Twilio's X-Twilio-Signature (Twilio has no nonce, so that argument is unused)."""
        try:
            return bool(RequestValidator(settings.TWILIO_AUTH_TOKEN).validate(url, params or {}, signature))
        except Exception:
            logger.warning("⚠️ [Twilio] Signature check raised an error")
            return False

    # ── Stream messages ───────────────────────────────────────────

    def parse_stream_event(self, msg: dict) -> StreamEvent | None:
        """Translate a Twilio Media Streams message (start, media, mark, dtmf, stop)."""
        event = msg.get("event")
        if event == "start":
            start = msg.get("start", {})
            params = start.get("customParameters") or {}
            return StreamEvent("start", call_id=start.get("callSid", ""),
                               stream_id=start.get("streamSid") or msg.get("streamSid", ""),
                               caller_phone=params.get("callerPhone", ""),
                               format_ok=is_expected_format(start.get("mediaFormat", {})))
        if event == "media":
            media = msg.get("media", {})
            if media.get("track", "inbound") != "inbound":
                return None
            return StreamEvent("media", audio=base64.b64decode(media.get("payload", "")))
        if event == "mark":
            return StreamEvent("mark", name=msg.get("mark", {}).get("name", ""))
        if event == "dtmf":
            return StreamEvent("dtmf")
        if event == "stop":
            return StreamEvent("stop")
        return None  # "connected" and anything else

    def audio_message(self, stream_id: str, ulaw: bytes) -> dict:
        """Twilio media message (payload is base64 mu-law, nothing else)."""
        return {"event": "media", "streamSid": stream_id,
                "media": {"payload": base64.b64encode(ulaw).decode()}}

    def mark_message(self, stream_id: str, name: str) -> dict:
        """Twilio mark message (Twilio echoes it back when the audio before it has played)."""
        return {"event": "mark", "streamSid": stream_id, "mark": {"name": name}}

    def clear_message(self, stream_id: str) -> dict:
        """Twilio clear message: empties the queued audio."""
        return {"event": "clear", "streamSid": stream_id}