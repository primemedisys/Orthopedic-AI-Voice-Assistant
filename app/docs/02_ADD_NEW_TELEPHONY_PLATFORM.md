# Adding or Switching a Telephony Platform

How to switch between the platforms already built (Twilio and Plivo) and how to add a new one (for example Vonage or Telnyx), step by step, with example code and a confirmation checklist.

---

## 1. How the design makes this easy

```text
 Phone provider  ──►  Adapter (translates)  ──►  CallSession (same for everyone)  ──►  Bodhan + LangGraph agent
 (Twilio/Plivo/…)     app/telephony/*_adapter.py   app/voice/session.py
```

- **`CallSession` never knows the provider.** It receives neutral events (`start`, `media`, `mark`, `dtmf`, `stop`) and sends neutral commands (play audio, set a marker, clear audio).
- **Each provider has one adapter** that translates its own XML and WebSocket messages into those neutral ones.
- **Each provider has one routes file** with its webhooks (`answer`, `stream`, `stream-status`, `transfer`, `transfer-status`).
- **`TELEPHONY_PROVIDER` in `.env`** decides which routes are mounted.

So a new provider needs: one adapter, one routes file, a few settings, and a small mount change. The agent, speech, database and session code stay untouched.

---

## 2. Switching between Twilio and Plivo (nothing new to build)

| Step | Twilio | Plivo |
|---|---|---|
| 1. `.env` | `TELEPHONY_PROVIDER=twilio` | `TELEPHONY_PROVIDER=plivo` |
| 2. Credentials | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, optional `TWILIO_NUMBER` | `PLIVO_AUTH_ID`, `PLIVO_AUTH_TOKEN`, `PLIVO_NUMBER` |
| 3. Signature check while testing | `TWILIO_VALIDATE_SIGNATURE=false` | `PLIVO_VALIDATE_SIGNATURE=false` |
| 4. Provider console webhook (POST) | `https://YOUR-URL/voice/twilio/answer` | `https://YOUR-URL/voice/plivo/answer` |
| 5. Restart the server **by hand** | yes | yes |
| 6. Confirm | `/docs` shows `/voice/twilio/*` | `/docs` shows `/voice/plivo/*` |

Also keep `PUBLIC_BASE_URL` equal to your current HTTPS URL, and set `ESCALATION_PHONE` (the doctor) to a phone that is **not** the one forwarding calls to the bot.

---

## 3. What differs between providers (reference)

This table is the checklist of facts you must find in a new provider's documentation.

| Concept | Plivo | Twilio |
|---|---|---|
| Call id in the answer webhook | `CallUUID` | `CallSid` |
| Caller number in the webhook | `From` (digits, no `+`) | `From` (E.164 with `+`) |
| Open the live stream | `<Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">wss://…</Stream>` | `<Connect><Stream url="wss://…"><Parameter name="callerPhone" value="…"/></Stream></Connect>` |
| Pass the caller number to the socket | `extraHeaders="callerPhone=…"` | `<Parameter>` (arrives as `start.customParameters`) |
| Start event | `event: start`, `start.callId`, `start.streamId`, `mediaFormat` | `event: start`, `start.callSid`, `start.streamSid`, `mediaFormat` |
| Caller audio | `event: media`, `media.payload` (base64 mu-law 8 kHz) | same |
| Play audio to caller | `{event: playAudio, media: {contentType, sampleRate, payload}}` | `{event: media, streamSid, media: {payload}}` |
| "Tell me when this audio finished" | send `checkpoint`, receive `playedStream` | send `mark`, receive the same `mark` back |
| Stop queued audio (interruption) | `clearAudio` | `clear` |
| Transfer a live call | REST `calls.transfer(call_uuid, legs="aleg", aleg_url=…)` | REST `calls(sid).update(url=…)` |
| Ring the doctor | `<Dial timeout callerId action>` + `<Number>` | same |
| Did the doctor answer? | `DialBLegUUID` is not empty | `DialCallStatus` is `completed` or `answered` |
| Speak a line with the provider's voice | `<Speak voice language>` | `<Say voice language>` |
| Webhook signature | V3: headers `X-Plivo-Signature-V3` and nonce | `X-Twilio-Signature` |

Provider-specific cautions found so far:

- **Twilio trial:** one number, inbound only from verified caller IDs, outbound only to verified numbers in your sign-up country, a short trial notice, limited minutes.
- **Plivo India:** India-registered business, an account created in the India data region (cannot be changed later), business KYC, and both call legs must stay inside India.

---

## 4. Adding a brand-new provider: step by step

Replace `acme` with the provider's name.

### Step 0: Read the provider's documentation first

Do not guess any of these. Confirm each one in the provider's current docs and write the answers down:

1. Can an inbound call be connected to a **bidirectional WebSocket audio stream**? What is the exact XML/JSON that starts it?
2. **Audio format**: mu-law 8 kHz is what the code expects. If the provider uses linear PCM or another rate, you also need a conversion (see section 7).
3. The exact **names and fields** of the WebSocket messages: start, media, mark/checkpoint, clear, stop, dtmf.
4. How to pass the **caller's number** to the stream.
5. How to **transfer or redirect** a live call (REST call, parameters).
6. How to **ring another number** and how the result is reported (the "did they answer" field).
7. How **webhook signatures** are computed, and whether the WebSocket handshake is signed.
8. Free-trial limits, and number and KYC requirements for your country.

### Step 1: Add settings (`app/config.py`)

```python
    # ── Telephony (Acme) ──────────────────────────────────────────
    ACME_API_KEY: str = ""
    ACME_API_SECRET: str = ""
    ACME_NUMBER: str = ""
    ACME_VALIDATE_SIGNATURE: bool = False
```

Make the startup warning provider-aware:

```python
    def missing_voice_settings(self) -> list[str]:
        """Names of voice settings that are still empty (used for a startup warning)."""
        required = {
            "BODHAN_TRANSCRIBE_API_KEY": self.stt_api_key,
            "BODHAN_SPEAK_API_KEY": self.tts_api_key,
            "BODHAN_VOICE": self.BODHAN_VOICE,
            "ESCALATION_PHONE": self.ESCALATION_PHONE,
        }
        provider = self.TELEPHONY_PROVIDER.lower()
        if provider == "twilio":
            required.update({"TWILIO_ACCOUNT_SID": self.TWILIO_ACCOUNT_SID,
                             "TWILIO_AUTH_TOKEN": self.TWILIO_AUTH_TOKEN})
        elif provider == "plivo":
            required.update({"PLIVO_AUTH_ID": self.PLIVO_AUTH_ID,
                             "PLIVO_AUTH_TOKEN": self.PLIVO_AUTH_TOKEN,
                             "PLIVO_NUMBER": self.PLIVO_NUMBER})
        elif provider == "acme":
            required.update({"ACME_API_KEY": self.ACME_API_KEY,
                             "ACME_API_SECRET": self.ACME_API_SECRET})
        return [name for name, value in required.items() if not value]
```

Add the same names to `.env` (values only there).

### Step 2: Install the provider's SDK

Add it to `requirements.txt` and run `pip install -r requirements.txt`. If the provider has no Python SDK, use `httpx` (already installed) for its REST calls.

### Step 3: Write the adapter (`app/telephony/acme_adapter.py`)

Copy `twilio_adapter.py` and change what the table in section 3 says. Skeleton:

```python
"""
app/telephony/acme_adapter.py
─────────────────────────────
Acme implementation: provider XML/JSON, WebSocket message translation, call transfer, signatures.
"""

import asyncio
import base64
import logging
from xml.sax.saxutils import escape, quoteattr

from app.config import settings
from app.telephony.base import StreamEvent, TelephonyAdapter, is_expected_format

logger = logging.getLogger("voice.acme")


def _wrap(*parts: str) -> str:
    """Put elements inside the provider's root element (adapt if the provider uses JSON instead of XML)."""
    return '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(parts) + "</Response>"


class AcmeAdapter(TelephonyAdapter):
    name = "acme"   # becomes the URL path: /voice/acme/...

    # ── Webhook responses ─────────────────────────────────────────

    def stream_xml(self, ws_url: str, status_url: str, caller_phone: str) -> str:
        """Instruction that opens a two-way audio stream to ws_url and passes the caller's number."""
        raise NotImplementedError  # build from the provider's docs

    def dial_xml(self, number: str, action_url: str, timeout: int) -> str:
        """Instruction that rings `number` and reports the result to action_url."""
        raise NotImplementedError

    def speak_hangup_xml(self, text: str) -> str:
        """Instruction that speaks `text` with the provider's voice, then hangs up."""
        raise NotImplementedError

    def hangup_xml(self) -> str:
        """Instruction that ends the call."""
        raise NotImplementedError

    # ── Live call control ─────────────────────────────────────────

    async def transfer_call(self, call_id: str, url: str) -> None:
        """Redirect the live call so the provider fetches new instructions from `url`."""
        raise NotImplementedError  # wrap blocking SDK calls with asyncio.to_thread(...)

    def validate_signature(self, method: str, url: str, nonce: str,
                           signature: str, params: dict | None = None) -> bool:
        """True if the request really came from the provider. Any error must return False."""
        raise NotImplementedError

    # ── WebSocket messages ────────────────────────────────────────

    def parse_stream_event(self, msg: dict) -> StreamEvent | None:
        """Translate one provider message into a StreamEvent. Return None for messages we ignore."""
        event = msg.get("event")
        if event == "start":
            start = msg.get("start", {})
            return StreamEvent("start",
                               call_id=start.get("CALL_ID_FIELD", ""),
                               stream_id=start.get("STREAM_ID_FIELD", ""),
                               caller_phone="",  # read the caller number the way the provider passes it
                               format_ok=is_expected_format(start.get("mediaFormat", {})))
        if event == "media":
            payload = msg.get("media", {}).get("payload", "")
            return StreamEvent("media", audio=base64.b64decode(payload))
        if event == "MARK_ECHO_EVENT":
            return StreamEvent("mark", name=msg.get("name", ""))
        if event == "stop":
            return StreamEvent("stop")
        return None

    def audio_message(self, stream_id: str, ulaw: bytes) -> dict:
        """Message that plays mu-law audio to the caller."""
        raise NotImplementedError

    def mark_message(self, stream_id: str, name: str) -> dict:
        """Message asking to be told when playback reaches this point."""
        raise NotImplementedError

    def clear_message(self, stream_id: str) -> dict:
        """Message that stops queued audio (used when the caller interrupts)."""
        raise NotImplementedError
```

The `StreamEvent` fields that **must** be filled are:

| Kind | Required fields |
|---|---|
| `start` | `call_id`, `stream_id`, `caller_phone` (or empty), `format_ok` |
| `media` | `audio` (raw mu-law bytes, not base64) |
| `mark` | `name` (the same name you sent in `mark_message`) |
| `dtmf`, `stop` | nothing |

If the provider returns JSON instead of XML for call control, the five "xml" methods can return JSON strings. In the routes file, return `Response(content=body, media_type="application/json")` instead of `text/xml`.

### Step 4: Register the adapter (`app/telephony/__init__.py`)

```python
from app.telephony.acme_adapter import AcmeAdapter

@lru_cache
def get_adapter() -> TelephonyAdapter:
    """Create the phone adapter once and reuse it."""
    provider = settings.TELEPHONY_PROVIDER.lower()
    if provider == "plivo":
        return PlivoAdapter()
    if provider == "twilio":
        return TwilioAdapter()
    if provider == "acme":
        return AcmeAdapter()
    raise RuntimeError(f"Unsupported TELEPHONY_PROVIDER: {settings.TELEPHONY_PROVIDER}")
```

### Step 5: Write the routes (`app/routes/v1/voice_acme.py`)

Copy `voice_twilio.py` and change four things:

1. `prefix="/voice/acme"` and `adapter = AcmeAdapter()`.
2. The form field names read in each endpoint (the provider's call id, caller number and dial-result fields; see the table in section 3).
3. The signature header name(s) and the `*_VALIDATE_SIGNATURE` setting.
4. The "did the doctor answer" test in `transfer-status`.

The answer webhook is the heart of it:

```python
@router.post("/answer")
async def answer(request: Request) -> Response:
    """Provider calls this when someone dials the clinic number. We reply with the stream instruction."""
    try:
        form = await _verified_form(request)
        call_id = form.get("CALL_ID_FIELD", "")
        caller = patient_service.normalize_phone(form.get("CALLER_FIELD", ""))  # add "+" first if the provider omits it
        register_caller(call_id, caller)            # fallback if the stream does not carry the number

        ws_url = _base().replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/voice/acme/stream"
        status_url = f"{_base()}/voice/acme/stream-status"
        logger.info("📞 [Voice] Incoming call %s", call_id)
        return _xml(adapter.stream_xml(ws_url, status_url, caller))
    except HTTPException:
        raise
    except Exception:
        logger.exception("❌ [Voice] Answer handler failed")
        return _xml(adapter.speak_hangup_xml(pick_reply("error")))   # never leave the caller in silence


@router.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    """Provider connects here and exchanges live audio. One CallSession handles the whole call."""
    await ws.accept()
    await CallSession(ws, ws.app.state.graph, adapter).run()
```

Keep the other three endpoints (`stream-status`, `transfer`, `transfer-status`) with the same logic: attempt 1 rings `ESCALATION_PHONE`, attempt 2 rings `BACKUP_PHONE`, and a failure plays the `transfer_failed` line and notifies the doctor. The transfer URL is built as `/voice/<adapter.name>/transfer`, so the `name` attribute must match the `prefix`.

### Step 6: Mount it (`app/main.py`)

Replace the provider `if` with a small map that fails loudly on a wrong name:

```python
from app.routes.v1 import chat, voice, voice_twilio, voice_acme

voice_routers = {
    "plivo": voice.router,
    "twilio": voice_twilio.router,
    "acme": voice_acme.router,
}
provider = settings.TELEPHONY_PROVIDER.lower()
if provider not in voice_routers:
    raise RuntimeError(f"Unsupported TELEPHONY_PROVIDER: {settings.TELEPHONY_PROVIDER}")

app.include_router(chat.router)
app.include_router(voice_routers[provider])
```

### Step 7: Teach the simulator the new format (`app/scripts/simulate_call.py`)

In `start_message()` and `media_message()` add an `if provider == "acme":` branch that returns the provider's start and media messages. In the `reader()` function, add the branch that collects the bot's audio messages and answers the mark/checkpoint messages. Then:

```powershell
python -m app.scripts.simulate_call acme +91XXXXXXXXXX caller_1.wav
```

Allow `acme` in the argument check at the bottom of the script.

### Step 8: Configure the provider console

- Point the number's incoming-call webhook to `https://YOUR-URL/voice/acme/answer` with the method the provider expects (usually POST).
- Make sure the webhook URL is HTTPS and `PUBLIC_BASE_URL` matches it exactly.

---

## 5. Confirmation checklist

Work through these in order. Stop at the first failure.

| # | Check | Expected |
|---|---|---|
| 1 | Server starts after the change | `✅ Agent graph ready`, and a `⚠️` line only for settings you have not filled yet |
| 2 | `http://localhost:8000/docs` | shows `/voice/acme/answer`, `/stream`, `/stream-status`, `/transfer`, `/transfer-status` |
| 3 | `python -m app.scripts.simulate_call acme ...` | greeting and replies saved as WAV files, no `❌` in the log |
| 4 | Webhook reachable | the ngrok inspector (`http://127.0.0.1:4040`) shows the provider's request and a 200 reply |
| 5 | Real call connects | `📞 [Voice] Incoming call …` then `✅ [Voice] Stream started …` |
| 6 | You hear the greeting | if silence, look for `❌ [TTS]` (Bodhan) or `Unexpected audio format` (audio format mismatch) |
| 7 | The bot hears you | `🗣️ [Voice] Caller spoke (N characters)`; if not, lower `VAD_RMS_THRESHOLD` |
| 8 | Interrupt the bot while it speaks | `✋ [Voice] Caller interrupted, clearing audio` and the audio stops |
| 9 | Say "I fell down and I can't walk" | `🚨 EMERGENCY` log, the transfer line plays, then the doctor's phone rings (or the "doctor busy" line) |
| 10 | Hang up | `🛑 stream ended`, `✅ [Summary] Saved …`, and a doctor alert in the log |
| 11 | Turn signature validation on | the same call still works with `*_VALIDATE_SIGNATURE=true` |

Test the doctor transfer with the doctor's phone **answered** and **unanswered**, and read the `Transfer attempt … status=…` log line. The "did they answer" test is the part most likely to differ between providers.

---

## 6. Common problems when adding a provider

| Symptom | Cause |
|---|---|
| Call connects, then hangs up at once | the answer webhook returned the wrong XML/JSON or a non-200 status; read the provider's debugger or the ngrok inspector |
| `Unexpected audio format from the provider` | the provider's stream is not mu-law 8 kHz; configure it that way, or add a conversion (section 7) |
| Greeting plays but the bot never hears you | the inbound audio message is not parsed as `media`, or the track is not "inbound" |
| Bot talks over itself or never stops when interrupted | wrong `clear_message`, or the provider does not echo the marker (the `mark` event) |
| Every caller is treated as a new patient | the caller number is not reaching `start` (check `extra_headers`/custom parameters and the fallback in `register_caller`) |
| Transfer works but the caller then hears "doctor busy" after talking to the doctor | the dial-result check treats "answered" as failed; log all fields of the action callback and fix the test |
| Webhooks rejected with 403 | signature check uses a different URL than the provider signed; the URL must be `PUBLIC_BASE_URL` + path (+ query) |
| The provider needs a different audio sample rate | convert in `audio_utils.py` (section 7) |

---

## 7. If the new provider does not use mu-law 8 kHz

Everything downstream expects 8 kHz mu-law because `audio_utils.py` and the voice detector assume it. If a provider sends linear 16-bit PCM or 16 kHz:

1. In `parse_stream_event`, convert the incoming bytes to 8 kHz mu-law before putting them in `StreamEvent.audio` (use `audioop.ratecv` and `audioop.lin2ulaw`).
2. In `audio_message`, convert our mu-law to the provider's format before encoding.
3. Set `format_ok` according to the provider's real format, and update `STREAM_ENCODING` and `STREAM_RATE` usage in `base.py` if the provider needs different values.

Keep the conversion inside the adapter so `CallSession` stays unchanged.

---

## 8. Quick reference: which file to touch

| Goal | Files |
|---|---|
| Switch between Twilio and Plivo | `.env` only (+ restart) |
| New provider | `config.py`, `.env`, `requirements.txt`, `telephony/<name>_adapter.py`, `telephony/__init__.py`, `routes/v1/voice_<name>.py`, `main.py`, `scripts/simulate_call.py` |
| Change what the bot says | `data/*.json` and `agent/prompts.py` |
| Change voice or language handling | `.env` (`BODHAN_*`), `speech/bodhan_tts.py` |
| Change emergency phrases | `data/emergency_rules.json` (doctor must approve) |
