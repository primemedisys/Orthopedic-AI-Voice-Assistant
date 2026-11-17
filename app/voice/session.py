# """
# app/voice/session.py
# ────────────────────
# One CallSession = one live phone call over the provider's WebSocket (Plivo or Twilio).

#   caller audio -> VAD (finds sentence end) -> Bodhan STT -> LangGraph -> Bodhan TTS -> audio back

# Rules:
#   - A failure in one turn never kills the call; the caller hears an approved fallback line.
#   - Transcripts are never written to the logs (patient privacy).
#   - When the agent says action == "transfer", the call is moved to the doctor.
#   - If the reply is slow, a short cached "one moment" clip plays so the line is never silent.
#   - All provider-specific message formats live in the adapter, not here.
# """

# import asyncio
# from email.mime import text
# import json
# import logging
# import time

# from fastapi import WebSocket

# from app.agent.loader import pick_reply, set_reply_language
# from app.config import settings
# from app.services import call_service, summary_service
# from app.speech.audio_utils import VoiceActivityDetector, ulaw_to_pcm, utterance_to_wav
# # from app.speech.bodhan_stt import detect_caller_language, transcribe
# # from app.speech.bodhan_tts import split_sentences, synthesize_ulaw
# from app.telephony.base import StreamEvent, TelephonyAdapter

# logger = logging.getLogger("voice.session")

# PLAY_CHUNK_BYTES = 3200  # 400 ms of 8 kHz mu-law audio per message (a multiple of 20 ms)

# # The Answer webhook always knows the caller's number. We keep it here briefly in case
# # the stream's start event does not carry it. Removed as soon as the stream starts.
# _CALLERS: dict[str, str] = {}


# def register_caller(call_id: str, phone: str) -> None:
#     """Remember a caller's number from the Answer webhook."""
#     _CALLERS[call_id] = phone


# class CallSession:
#     """Handles everything that happens on one call's audio stream."""

#     def __init__(self, ws: WebSocket, graph, adapter: TelephonyAdapter) -> None:
#         self.ws = ws
#         self.graph = graph
#         self.adapter = adapter
#         self.call_id: str | None = None
#         self.stream_id: str | None = None
#         self.phone = "unknown"
#         self.lang = settings.DEFAULT_LANGUAGE    

#         self.vad = VoiceActivityDetector(
#             rms_threshold=settings.VAD_RMS_THRESHOLD,
#             end_silence_ms=settings.VAD_END_SILENCE_MS,
#             min_speech_ms=settings.VAD_MIN_SPEECH_MS,
#             max_utterance_ms=settings.VAD_MAX_UTTERANCE_S * 1000,
#         )
#         self.turns: asyncio.Queue = asyncio.Queue()   # sentences waiting to be answered (None = greeting)
#         self.speaking = False                         # True while the bot's audio is playing
#         self._played: dict[str, asyncio.Event] = {}   # marker name -> "finished playing" event
#         self._marker_no = 0
#         self._generation = 0                          # bumped on interruption to cancel pending speech
#         self._transferred = False

#         self._t_start = 0.0                           # when the stream started (for the greeting timing)
#         self._audio_started = False                   # True once this turn's real reply audio is being sent
#         self._first_audio_at: float | None = None     # when the first reply audio was sent
#         self._filler_played = False

#     # ==================================================
#     # MAIN LOOP
#     # ==================================================

#     async def run(self) -> None:
#         """Read events from the provider until the call ends, then save the call summary."""
#         worker = asyncio.create_task(self._turn_worker())
#         try:
#             async for raw in self.ws.iter_text():
#                 event = self.adapter.parse_stream_event(json.loads(raw))
#                 if event:
#                     await self._on_event(event)
#         except Exception:
#             logger.exception("❌ [Voice] Stream loop crashed")
#         finally:
#             worker.cancel()
#             logger.info("👋 [Voice] Stream closed")
#             await self._finish()

#     async def _on_event(self, event: StreamEvent) -> None:
#         """Route one neutral event (media is the most frequent, so it is checked first)."""
#         if event.kind == "media":
#             await self._on_media(event)
#         elif event.kind == "start":
#             await self._on_start(event)
#         elif event.kind == "mark":
#             self._on_played(event.name)
#         elif event.kind == "dtmf":
#             logger.info("🔢 [Voice] A key was pressed")
#         elif event.kind == "stop":
#             logger.info("🛑 [Voice] Provider ended the stream")

#     async def _on_start(self, event: StreamEvent) -> None:
#         """Stream began: check the audio format, find the caller's number, log the call, queue the greeting."""
#         self.call_id = event.call_id
#         self.stream_id = event.stream_id
#         self._t_start = time.perf_counter()

#         if not event.format_ok:
#             logger.error("❌ [Voice] Unexpected audio format from the provider")
#             await self.ws.close(code=1003)
#             return

#         fallback = _CALLERS.pop(self.call_id or "", "")
#         self.phone = event.caller_phone or fallback or "unknown"
#         if self.phone == "unknown":
#             logger.warning("⚠️ [Voice] Caller number missing, treating the caller as new")

#         await asyncio.to_thread(call_service.start_call, self.call_id, self.phone)
#         self.turns.put_nowait(None)  # None means "say the greeting"
#         logger.info("✅ [Voice] Stream started for call %s", self.call_id)

#     async def _on_media(self, event: StreamEvent) -> None:
#         """Caller audio arrived: run it through the VAD, handle interruption, queue finished sentences."""
#         if self._transferred or not event.audio:
#             return

#         utterance = self.vad.feed(ulaw_to_pcm(event.audio))

#         if self.speaking and self.vad.in_speech and self.vad.speech_ms >= settings.BARGE_IN_MS:
#             await self._barge_in()
#         if utterance:
#             self.turns.put_nowait(utterance)

#     def _on_played(self, name: str) -> None:
#         """The provider finished playing audio up to a marker."""
#         event = self._played.pop(name, None)
#         if event:
#             event.set()
#         if not self._played:
#             self.speaking = False

#     async def _barge_in(self) -> None:
#         """Caller started talking over the bot: stop the audio immediately and drop any reply still being prepared."""
#         logger.info("✋ [Voice] Caller interrupted, clearing audio")
#         self._generation += 1
#         self.speaking = False
#         await self._send(self.adapter.clear_message(self.stream_id))
#         for event in self._played.values():
#             event.set()
#         self._played.clear()

#     # ==================================================
#     # TURN HANDLING
#     # ==================================================

#     async def _turn_worker(self) -> None:
#         """Answer queued sentences one at a time. An error in one turn is logged and the next one still works."""
#         while True:
#             item = await self.turns.get()
#             if self._transferred:
#                 continue
#             try:
#                 if item is None:
#                     await self._greet()
#                 else:
#                     await self._handle_turn(item)
#             except Exception:
#                 logger.exception("❌ [Voice] Turn failed")

#     async def _greet(self) -> None:
#         """Speak the greeting and log how long the caller waited for the first sound."""
#         self._audio_started = False
#         self._first_audio_at = None
#         # greeting = pick_reply("greeting")
#         greeting = pick_reply("greeting", self.lang)
#         if settings.LOG_TRANSCRIPTS:
#             logger.info("🤖 [Bot] %s", greeting)
#         await self._speak(greeting)
        
#         if self._first_audio_at:
#             logger.info("⏱️ [Voice] greeting: first audio %.1fs after the stream started",
#                         self._first_audio_at - self._t_start)

#     async def _handle_turn(self, pcm_8k: bytes) -> None:
#         """One caller turn: speech to text, LangGraph, text to speech, with timing logs and a safe fallback at each step."""
#         t0 = time.perf_counter()
#         turn_gen = self._generation                    # if the caller interrupts later, this reply is dropped
#         self._audio_started = False
#         self._first_audio_at = None
#         self._filler_played = False
        
#         # Acknowledge only long sentences, a limited number of times, never two turns in a row
#         speech_s = len(pcm_8k) / 16000                 # 8 kHz x 2 bytes = 16,000 bytes per second
#         self._turns_since_filler += 1
#         use_filler = (settings.FILLER_ENABLED
#                       and speech_s >= settings.FILLER_MIN_SPEECH_S
#                       and self._fillers_used < settings.FILLER_MAX_PER_CALL
#                       and self._turns_since_filler >= 2)
        
#         filler = asyncio.create_task(self._filler_after_delay(turn_gen)) if use_filler else None
#         try:
#             # 1) speech to text
#             try:
#                 text = await transcribe(utterance_to_wav(pcm_8k))
#             except Exception:
#                 logger.exception("❌ [Voice] Speech-to-text failed")
#                 text = ""
#             t_stt = time.perf_counter()
#             if not text:
#                 await self._speak(pick_reply("not_understood", self.lang), turn_gen)
#                 return
            
#             self._update_language(text)
#             set_reply_language(self.lang) 
            
#             if settings.LOG_TRANSCRIPTS:
#                 logger.info("🗣️ [Caller] %s", text)
#             else:
#                 logger.info("🗣️ [Voice] Caller spoke (%s characters)", len(text))

#             # 2) the agent
#             try:
#                 result = await asyncio.to_thread(
#                     self.graph.invoke,
#                     {"messages": [("user", text)], "caller_phone": self.phone, "action": None},
#                     {"configurable": {"thread_id": self.call_id, "caller_phone": self.phone}},
#                 )
#             except Exception:
#                 logger.exception("❌ [Voice] Agent failed")
#                 await self._speak(pick_reply("error", self.lang), turn_gen)
#                 return
#             t_agent = time.perf_counter()

#             # 3) text to speech
#             reply = (result["messages"][-1].content or "").strip() or pick_reply("error", self.lang)
#             if settings.LOG_TRANSCRIPTS:
#                 logger.info("🤖 [Bot] %s", reply)
                
#             marker = await self._speak(reply, turn_gen)

#             first = f"{self._first_audio_at - t0:.1f}s" if self._first_audio_at else "none"
#             logger.info("⏱️ [Voice] stt %.1fs | agent %.1fs | first reply audio %s after speech ended "
#                         "(+%.1fs end-of-speech wait) | filler %s",
#                         t_stt - t0, t_agent - t_stt, first, settings.VAD_END_SILENCE_MS / 1000,
#                         "played" if self._filler_played else "not needed")

#             if result.get("action") == "transfer":
#                 await self._transfer(marker)
#         finally:
#             if filler:
#                 filler.cancel()

#     async def _filler_after_delay(self, generation: int) -> None:
#         """If no reply audio has started after FILLER_AFTER_MS, play a short cached acknowledgement in the caller's language."""
#         try:
#             await asyncio.sleep(settings.FILLER_AFTER_MS / 1000)
#             if self._audio_started or generation != self._generation or self._transferred:
#                 return
#             ulaw = await synthesize_ulaw(pick_reply("filler", self.lang))   # cached at startup, so it starts instantly
#             if self._audio_started or generation != self._generation:
#                 return                                                      # the real reply got there first
#             await self._send_audio(ulaw)
#             self.speaking = True
#             self._filler_played = True
#             self._fillers_used += 1
#             self._turns_since_filler = 0
#             self._marker_no += 1
#             name = f"filler-{self._marker_no}"
#             self._played[name] = asyncio.Event()
#             await self._send(self.adapter.mark_message(self.stream_id, name))
#         except asyncio.CancelledError:
#             raise
#         except Exception:
#             logger.warning("⚠️ [Voice] Filler clip failed")

#     async def _transfer(self, marker: str | None) -> None:
#         """Emergency: let the transfer line finish playing, then move the call to the doctor's number."""
#         self._transferred = True
#         if marker:
#             await self._wait_played(marker)
#         url = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/voice/{self.adapter.name}/transfer"
#         try:
#             await self.adapter.transfer_call(self.call_id, url)
#         except Exception:
#             logger.exception("❌ [Voice] Could not transfer the call")
#             self._transferred = False
#             await self._speak(pick_reply("transfer_failed", self.lang))  # the callback was already saved by the agent

#     # ==================================================
#     # SPEAKING
#     # ==================================================

#     def _update_language(self, text: str) -> None:
#         """Follow the caller's language. The first clear sentence sets it; a switch needs two clear sentences in a row."""
#         detected = detect_caller_language(text)
#         if not detected or detected == self.lang:
#             self._pending_lang = None
#         elif not self._lang_set or detected == self._pending_lang:
#             self.lang, self._lang_set, self._pending_lang = detected, True, None
#             logger.info("🌐 [Voice] Call language: %s", self.lang)
#         else:
#             self._pending_lang = detected
    
#     async def _speak(self, text: str, generation: int | None = None) -> str | None:
#         """Generate and play one sentence at a time."""
#         generation = self._generation if generation is None else generation
#         sent = False

#         for piece in split_sentences(text):
#             if generation != self._generation:
#                 break

#             try:
#                 ulaw = await synthesize_ulaw(piece)
#             except Exception:
#                 logger.exception("❌ [Voice] Text-to-speech failed")
#                 break

#             if generation != self._generation:
#                 break

#             if not self._audio_started:
#                 self._audio_started = True
#                 self._first_audio_at = time.perf_counter()

#             await self._send_audio(ulaw)
#             self.speaking = True
#             sent = True

#         if not sent:
#             return None

#         self._marker_no += 1
#         name = f"reply-{self._marker_no}"
#         self._played[name] = asyncio.Event()

#         await self._send(self.adapter.mark_message(self.stream_id, name))
#         return name

#     async def _send_audio(self, ulaw: bytes) -> None:
#         """Send audio to the caller in 400 ms chunks."""
#         for i in range(0, len(ulaw), PLAY_CHUNK_BYTES):
#             await self._send(self.adapter.audio_message(self.stream_id, ulaw[i:i + PLAY_CHUNK_BYTES]))

#     async def _wait_played(self, name: str, timeout: float = 20) -> None:
#         """Wait until the provider says the marker was reached (or give up after `timeout` seconds)."""
#         event = self._played.get(name)
#         if event:
#             try:
#                 await asyncio.wait_for(event.wait(), timeout)
#             except asyncio.TimeoutError:
#                 logger.warning("⚠️ [Voice] Playback confirmation timed out")

#     async def _send(self, payload: dict) -> None:
#         """Send one JSON message to the provider."""
#         await self.ws.send_text(json.dumps(payload))

#     # ==================================================
#     # CALL END
#     # ==================================================

#     async def _finish(self) -> None:
#         """Call ended: write the summary and alert the doctor if the call needs attention."""
#         if not self.call_id:
#             return
#         try:
#             await asyncio.to_thread(summary_service.finish_call, self.graph, self.call_id)
#         except Exception:
#             logger.exception("❌ [Voice] Could not save the call summary")




"""
app/voice/session.py
────────────────────
One CallSession = one live phone call over the provider's WebSocket (Plivo or Twilio).

  caller audio -> VAD (finds sentence end) -> speech-to-text -> LangGraph -> text-to-speech -> audio back

Rules:
  - A failure in one turn never kills the call; the caller hears an approved fallback line.
  - Transcripts are logged only when LOG_TRANSCRIPTS=true (testing).
  - The call starts in DEFAULT_LANGUAGE (Odia) and follows the caller's language.
  - action == "transfer" moves the call to the doctor.
  - A short acknowledgement plays only after a long caller sentence when the reply is slow.
  - All provider-specific message formats live in the adapter, not here.
"""

import asyncio
import json
import logging
import time

from fastapi import WebSocket

from app.agent.loader import pick_reply, set_reply_language
from app.config import settings
from app.services import call_service, summary_service
from app.speech.audio_utils import VoiceActivityDetector, ulaw_to_pcm, utterance_to_wav
from app.speech.factory import synthesize_ulaw, transcribe
from app.speech.text_utils import detect_caller_language, split_sentences
from app.telephony.base import StreamEvent, TelephonyAdapter

logger = logging.getLogger("voice.session")

PLAY_CHUNK_BYTES = 3200  # 400 ms of 8 kHz mu-law audio per message

# The Answer webhook knows the caller's number; keep it briefly in case the stream start lacks it.
_CALLERS: dict[str, str] = {}


def register_caller(call_id: str, phone: str) -> None:
    """Remember a caller's number from the Answer webhook."""
    _CALLERS[call_id] = phone


class CallSession:
    """Handles everything that happens on one call's audio stream."""

    def __init__(self, ws: WebSocket, graph, adapter: TelephonyAdapter) -> None:
        self.ws = ws
        self.graph = graph
        self.adapter = adapter
        self.call_id: str | None = None
        self.stream_id: str | None = None
        self.phone = "unknown"

        self.vad = VoiceActivityDetector(
            rms_threshold=settings.VAD_RMS_THRESHOLD,
            end_silence_ms=settings.VAD_END_SILENCE_MS,
            min_speech_ms=settings.VAD_MIN_SPEECH_MS,
            max_utterance_ms=settings.VAD_MAX_UTTERANCE_S * 1000,
        )
        self.turns: asyncio.Queue = asyncio.Queue()  # Queued caller turns; None means greeting
        self.speaking = False
        self._played: dict[str, asyncio.Event] = {}  # Marker name -> playback completed
        self._marker_no = 0
        self._generation = 0                         # Incremented on interruption to cancel pending speech
        self._transferred = False

        self._t_start = 0.0
        self._audio_started = False
        self._first_audio_at: float | None = None
        self._filler_played = False
        self._fillers_used = 0
        self._turns_since_filler = 99

        self.lang = settings.DEFAULT_LANGUAGE        # Current call language: or, hi, or en
        self._lang_set = False
        self._pending_lang: str | None = None

    # ==================================================
    # MAIN LOOP
    # ==================================================

    async def run(self) -> None:
        """Read events from the provider until the call ends, then save the summary."""
        worker = asyncio.create_task(self._turn_worker())
        try:
            async for raw in self.ws.iter_text():
                event = self.adapter.parse_stream_event(json.loads(raw))
                if event:
                    await self._on_event(event)
        except Exception:
            logger.exception("❌ [Voice] Stream loop crashed")
        finally:
            worker.cancel()
            logger.info("👋 [Voice] Stream closed")
            await self._finish()

    async def _on_event(self, event: StreamEvent) -> None:
        """Route one neutral event (media is the most frequent, so it is first)."""
        if event.kind == "media":
            await self._on_media(event)
        elif event.kind == "start":
            await self._on_start(event)
        elif event.kind == "mark":
            self._on_played(event.name)
        elif event.kind == "dtmf":
            logger.info("🔢 [Voice] A key was pressed")
        elif event.kind == "stop":
            logger.info("🛑 [Voice] Provider ended the stream")

    async def _on_start(self, event: StreamEvent) -> None:
        """Stream began: check the audio format, find the caller's number, log the call, queue the greeting."""
        self.call_id = event.call_id
        self.stream_id = event.stream_id
        self._t_start = time.perf_counter()

        if not event.format_ok:
            logger.error("❌ [Voice] Unexpected audio format from the provider")
            await self.ws.close(code=1003)
            return

        fallback = _CALLERS.pop(self.call_id or "", "")
        self.phone = event.caller_phone or fallback or "unknown"
        if self.phone == "unknown":
            logger.warning("⚠️ [Voice] Caller number missing, treating the caller as new")

        await asyncio.to_thread(call_service.start_call, self.call_id, self.phone)
        self.turns.put_nowait(None)  # None means "say the greeting"
        logger.info("✅ [Voice] Stream started for call %s", self.call_id)

    async def _on_media(self, event: StreamEvent) -> None:
        """Caller audio: run the VAD, handle interruption, queue finished sentences."""
        if self._transferred or not event.audio:
            return
        utterance = self.vad.feed(ulaw_to_pcm(event.audio))

        if self.speaking and self.vad.in_speech and self.vad.speech_ms >= settings.BARGE_IN_MS:
            await self._barge_in()
        if utterance:
            self.turns.put_nowait(utterance)

    def _on_played(self, name: str) -> None:
        """The provider finished playing audio up to a marker."""
        event = self._played.pop(name, None)
        if event:
            event.set()
        if not self._played:
            self.speaking = False

    async def _barge_in(self) -> None:
        """Caller talked over the bot: stop the audio now and drop any reply still being prepared."""
        logger.info("✋ [Voice] Caller interrupted, clearing audio")
        self._generation += 1
        self.speaking = False
        await self._send(self.adapter.clear_message(self.stream_id))
        for event in self._played.values():
            event.set()
        self._played.clear()

    # ==================================================
    # LANGUAGE
    # ==================================================

    def _update_language(self, text: str, provider_lang: str | None) -> None:
        """Follow the caller's language: the first clear sentence sets it, a switch needs two in a row."""
        detected = provider_lang or detect_caller_language(text)
        if not detected or detected == self.lang:
            self._pending_lang = None
        elif not self._lang_set or detected == self._pending_lang:
            self.lang, self._lang_set, self._pending_lang = detected, True, None
            logger.info("🌐 [Voice] Call language: %s", self.lang)
        else:
            self._pending_lang = detected

    # ==================================================
    # TURN HANDLING
    # ==================================================

    async def _turn_worker(self) -> None:
        """Answer queued sentences one at a time; an error in one turn never stops the next."""
        while True:
            item = await self.turns.get()
            if self._transferred:
                continue
            try:
                if item is None:
                    await self._greet()
                else:
                    await self._handle_turn(item)
            except Exception:
                logger.exception("❌ [Voice] Turn failed")

    async def _greet(self) -> None:
        """Speak the greeting in the default language and log how long the caller waited for the first sound."""
        self._audio_started = False
        self._first_audio_at = None
        greeting = pick_reply("greeting", self.lang)
        if settings.LOG_TRANSCRIPTS:
            logger.info("🤖 [Bot] %s", greeting)
        await self._speak(greeting)
        if self._first_audio_at:
            logger.info(
                "⏱️ [Voice] greeting: first audio %.1fs after the stream started",
                self._first_audio_at - self._t_start,
            )

    async def _handle_turn(self, pcm_8k: bytes) -> None:
        """One caller turn: speech to text, LangGraph, text to speech, with timing logs and safe fallbacks."""
        t0 = time.perf_counter()
        turn_gen = self._generation
        self._audio_started = False
        self._first_audio_at = None
        self._filler_played = False

        speech_s = len(pcm_8k) / 16000  # 8 kHz, 16-bit PCM = 16,000 bytes/second
        self._turns_since_filler += 1
        use_filler = (
            settings.FILLER_ENABLED
            and speech_s >= settings.FILLER_MIN_SPEECH_S
            and self._fillers_used < settings.FILLER_MAX_PER_CALL
            and self._turns_since_filler >= 2
        )
        filler = asyncio.create_task(self._filler_after_delay(turn_gen)) if use_filler else None
        try:
            # 1) speech to text
            try:
                transcript = await transcribe(utterance_to_wav(pcm_8k))
            except Exception:
                logger.exception("❌ [Voice] Speech-to-text failed")
                transcript = None
            text = transcript.text if transcript else ""
            t_stt = time.perf_counter()
            if not text:
                await self._speak(pick_reply("not_understood", self.lang), turn_gen)
                return

            self._update_language(text, transcript.language)
            set_reply_language(self.lang)               # the agent's prompt and fixed lines follow this

            if settings.LOG_TRANSCRIPTS:
                logger.info("🗣️ [Caller] %s", text)
            else:
                logger.info("🗣️ [Voice] Caller spoke (%s characters)", len(text))

            # 2) the agent
            try:
                result = await asyncio.to_thread(
                    self.graph.invoke,
                    {"messages": [("user", text)], "caller_phone": self.phone, "action": None},
                    {"configurable": {"thread_id": self.call_id, "caller_phone": self.phone}},
                )
            except Exception:
                logger.exception("❌ [Voice] Agent failed")
                await self._speak(pick_reply("error", self.lang), turn_gen)
                return
            t_agent = time.perf_counter()

            # 3) text to speech
            reply = (result["messages"][-1].content or "").strip() or pick_reply("error", self.lang)
            if settings.LOG_TRANSCRIPTS:
                logger.info("🤖 [Bot] %s", reply)
            marker = await self._speak(reply, turn_gen)

            first = f"{self._first_audio_at - t0:.1f}s" if self._first_audio_at else "none"
            logger.info(
                "⏱️ [Voice] stt %.1fs | agent %.1fs | first reply audio %s after speech ended "
                "(+%.1fs end-of-speech wait) | filler %s",
                t_stt - t0,
                t_agent - t_stt,
                first,
                settings.VAD_END_SILENCE_MS / 1000,
                "played" if self._filler_played else "not used",
            )

            if result.get("action") == "transfer":
                await self._transfer(marker)
        finally:
            if filler:
                filler.cancel()

    async def _filler_after_delay(self, generation: int) -> None:
        """If no reply audio started after FILLER_AFTER_MS, play a short cached acknowledgement in the caller's language."""
        try:
            await asyncio.sleep(settings.FILLER_AFTER_MS / 1000)
            if self._audio_started or generation != self._generation or self._transferred:
                return
            ulaw = await synthesize_ulaw(pick_reply("filler", self.lang))   # cached, so it starts instantly
            if self._audio_started or generation != self._generation:
                return
            await self._send_audio(ulaw)
            self.speaking = True
            self._filler_played = True
            self._fillers_used += 1
            self._turns_since_filler = 0
            self._marker_no += 1
            name = f"filler-{self._marker_no}"
            self._played[name] = asyncio.Event()
            await self._send(self.adapter.mark_message(self.stream_id, name))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("⚠️ [Voice] Filler clip failed")

    async def _transfer(self, marker: str | None) -> None:
        """Emergency: let the transfer line finish, then move the call to the doctor's number."""
        self._transferred = True
        if marker:
            await self._wait_played(marker)
        url = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/voice/{self.adapter.name}/transfer"
        try:
            await self.adapter.transfer_call(self.call_id, url)
        except Exception:
            logger.exception("❌ [Voice] Could not transfer the call")
            self._transferred = False
            await self._speak(pick_reply("transfer_failed", self.lang))

    # ==================================================
    # SPEAKING
    # ==================================================

    async def _speak(self, text: str, generation: int | None = None) -> str | None:
        """Make speech for `text` (pieces in parallel, played in order). Returns a marker name, or None if nothing played.
        If the caller interrupted since `generation`, nothing is played."""
        generation = self._generation if generation is None else generation
        sent = False
        tasks = [asyncio.create_task(synthesize_ulaw(piece)) for piece in split_sentences(text)]
        try:
            for task in tasks:
                if generation != self._generation:
                    break
                try:
                    ulaw = await task
                except Exception:
                    logger.exception("❌ [Voice] Text-to-speech failed")
                    break
                if generation != self._generation:
                    break
                if not self._audio_started:
                    self._audio_started = True
                    self._first_audio_at = time.perf_counter()
                await self._send_audio(ulaw)
                self.speaking = True
                sent = True
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        if not sent:
            return None
        self._marker_no += 1
        name = f"reply-{self._marker_no}"
        self._played[name] = asyncio.Event()
        await self._send(self.adapter.mark_message(self.stream_id, name))
        return name

    async def _send_audio(self, ulaw: bytes) -> None:
        """Send audio to the caller in 400 ms chunks."""
        for i in range(0, len(ulaw), PLAY_CHUNK_BYTES):
            await self._send(self.adapter.audio_message(self.stream_id, ulaw[i:i + PLAY_CHUNK_BYTES]))

    async def _wait_played(self, name: str, timeout: float = 20) -> None:
        """Wait until the provider says the marker was reached (or give up after `timeout` seconds)."""
        event = self._played.get(name)
        if event:
            try:
                await asyncio.wait_for(event.wait(), timeout)
            except asyncio.TimeoutError:
                logger.warning("⚠️ [Voice] Playback confirmation timed out")

    async def _send(self, payload: dict) -> None:
        """Send one JSON message to the provider."""
        await self.ws.send_text(json.dumps(payload))

    # ==================================================
    # CALL END
    # ==================================================

    async def _finish(self) -> None:
        """Call ended: write the summary and alert the doctor if the call needs attention."""
        if not self.call_id:
            return
        try:
            await asyncio.to_thread(summary_service.finish_call, self.graph, self.call_id)
        except Exception:
            logger.exception("❌ [Voice] Could not save the call summary")