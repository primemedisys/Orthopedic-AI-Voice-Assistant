"""
app/routes/v1/voice.py
──────────────────────
Plivo-facing endpoints.

  POST /voice/plivo/answer           Answer URL: returns the <Stream> XML
  WS   /voice/plivo/stream           the live audio WebSocket (one CallSession per call)
  POST /voice/plivo/stream-status    stream started / stopped / failed (log only)
  POST /voice/plivo/transfer         XML that rings the doctor (used after an emergency)
  POST /voice/plivo/transfer-status  what happened when ringing: connected, try backup, or give up
"""

import logging

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket

from app.agent.loader import pick_reply
from app.config import settings
from app.services import patient_service
from app.services.notification_service import notify_doctor
# from app.telephony import get_adapter
from app.telephony.plivo.plivo_adapter import PlivoAdapter
from app.voice.session import CallSession, register_caller

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice/plivo", tags=["voice"])
# adapter = get_adapter()
adapter = PlivoAdapter()


# ==================================================
# HELPERS
# ==================================================


def _base() -> str:
    """Public base URL without a trailing slash."""
    return settings.PUBLIC_BASE_URL.rstrip("/")


def _xml(body: str) -> Response:
    """Wrap XML text in an HTTP response Plivo accepts."""
    return Response(content=body, media_type="text/xml")


def _e164(raw: str) -> str:
    """Plivo sends numbers like 919999900001 (no plus). Convert to +919999900001."""
    raw = (raw or "").strip()
    if raw and not raw.startswith("+"):
        raw = "+" + raw
    return patient_service.normalize_phone(raw)


def _public_url(request: Request) -> str:
    """The URL exactly as Plivo called it (needed to verify the signature behind ngrok/proxies)."""
    url = _base() + request.url.path
    return f"{url}?{request.url.query}" if request.url.query else url


async def _verified_form(request: Request) -> dict:
    """Read Plivo's form data and, if enabled, reject requests with a bad signature (403)."""
    form = dict(await request.form())
    if settings.PLIVO_VALIDATE_SIGNATURE:
        ok = adapter.validate_signature(
            "POST",
            _public_url(request),
            request.headers.get("x-plivo-signature-v3-nonce", ""),
            request.headers.get("x-plivo-signature-v3", ""),
            form,
        )
        if not ok:
            logger.warning("❌ [Voice] Rejected a request with an invalid Plivo signature")
            raise HTTPException(status_code=403, detail="❌ Invalid Plivo signature")
    return form


def _ws_signature_ok(ws: WebSocket) -> bool:
    """Check the WebSocket upgrade signature (we try both https:// and wss:// forms of the URL)."""
    signature = ws.headers.get("x-plivo-signature-v3", "")
    nonce = ws.headers.get("x-plivo-signature-v3-nonce", "")
    host = ws.headers.get("host", "")
    return any(
        adapter.validate_signature("GET", f"{scheme}://{host}{ws.url.path}", nonce, signature)
        for scheme in ("https", "wss")
    )


def _dial_response(number: str, attempt: int) -> Response:
    """XML that rings `number` and reports back to /transfer-status."""
    action = f"{_base()}/voice/plivo/transfer-status?attempt={attempt}"
    return _xml(adapter.dial_xml(number, action, settings.TRANSFER_RING_SECONDS))


def _failed_response() -> Response:
    """Tell the caller the doctor is busy (callback is already saved) and hang up."""
    return _xml(adapter.speak_hangup_xml(pick_reply("transfer_failed")))


# ==================================================
# ENDPOINTS
# ==================================================


@router.post("/answer")
async def answer(request: Request) -> Response:
    """Plivo calls this when someone dials the clinic number. We reply with XML that opens the audio stream."""
    try:
        form = await _verified_form(request)
        call_uuid = form.get("CallUUID", "")
        caller = _e164(form.get("From", ""))
        register_caller(call_uuid, caller)

        ws_url = _base().replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/voice/plivo/stream"
        status_url = f"{_base()}/voice/plivo/stream-status"
        logger.info("📞 [Voice] Incoming call %s", call_uuid)
        return _xml(adapter.stream_xml(ws_url, status_url, caller))
    except HTTPException:
        raise
    except Exception:
        logger.exception("❌ [Voice] Answer handler failed")
        return _xml(adapter.speak_hangup_xml(pick_reply("error")))


@router.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    """Plivo connects here and exchanges live audio. The whole call is handled by one CallSession."""
    if settings.PLIVO_VALIDATE_SIGNATURE and not _ws_signature_ok(ws):
        logger.warning("❌ [Voice] WebSocket rejected: invalid signature")
        await ws.close(code=1008)
        return
    await ws.accept()
    await CallSession(ws, ws.app.state.graph, adapter).run()


@router.post("/stream-status")
async def stream_status(request: Request) -> dict:
    """Plivo reports stream events (started, stopped, failed). We only log them."""
    form = await _verified_form(request)
    logger.info("📡 [Voice] Stream %s (%s)", form.get("Event"), form.get("StatusReason", "-"))
    return {"status": "success", "message": "✅ Received"}


@router.post("/transfer")
async def transfer(request: Request, attempt: int = 1) -> Response:
    """After an emergency the call is moved here. Attempt 1 rings the doctor, attempt 2 rings the backup."""
    try:
        await _verified_form(request)
        number = settings.ESCALATION_PHONE if attempt == 1 else settings.BACKUP_PHONE
        if not number:
            logger.error("❌ [Voice] No phone number configured for transfer attempt %s", attempt)
            return _failed_response()
        return _dial_response(number, attempt)
    except HTTPException:
        raise
    except Exception:
        logger.exception("❌ [Voice] Transfer handler failed")
        return _failed_response()


@router.post("/transfer-status")
async def transfer_status(request: Request, attempt: int = 1) -> Response:
    """Plivo reports how ringing went. Answered: finish. No answer: try the backup, then the 'doctor is busy' line."""
    try:
        form = await _verified_form(request)
        answered = bool(form.get("DialBLegUUID"))
        logger.info("📞 [Voice] Transfer attempt %s: status=%s answered=%s",
                    attempt, form.get("DialStatus"), answered)

        if answered:
            return _xml(adapter.hangup_xml())
        if attempt == 1 and settings.BACKUP_PHONE:
            return _dial_response(settings.BACKUP_PHONE, 2)

        notify_doctor(f"Could not reach you for an emergency call from {form.get('From', 'unknown')}. "
                      "A callback is saved, please call back now.", level="emergency")
        return _failed_response()
    except HTTPException:
        raise
    except Exception:
        logger.exception("❌ [Voice] Transfer status handler failed")
        return _failed_response()