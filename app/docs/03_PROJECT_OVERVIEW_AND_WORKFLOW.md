# Orthopedic AI Voice Agent: Overview and Workflow

What the project is for, who it serves, and how the whole flow works from the first ring to the doctor's alert.

---

## 1. Main aim

Dr. Pushpak Samal, an orthopedic doctor in Bhubaneswar, does not have time to talk to every patient. The agent answers his clinic number and does the receptionist's work:

1. **Talk to patients naturally** (English, Hindi, Odia, or a mix) and collect what they need.
2. **Recognise emergencies** and put the patient through to the doctor immediately.
3. **Never let a patient be lost.** If the doctor is busy, a callback is saved and he is told.
4. **Give the doctor a short summary** of every call that matters, so he talks to patients only when it counts.

It is an administrative assistant. It does **not** diagnose, prescribe, or decide on surgery.

---

## 2. Who uses it

| Person | What they get |
|---|---|
| **Patient (existing)** | verified by name and date of birth, then can describe problems and ask questions |
| **Patient (new)** | greeted, asked for name, date of birth and what happened, and promised a callback |
| **Doctor** | emergency calls ring his phone; a summary of urgent calls and new patients; a callback list |
| **Clinic staff** | callback requests to action (viewing them is a pending feature) |

---

## 3. What the bot can and cannot do

| The bot CAN | The bot MUST NEVER |
|---|---|
| Greet, say it is an AI assistant, and mention the call may be recorded | Diagnose a condition |
| Share clinic information from the approved data (address, timings, fees, ACL/MCL surgery cost) | Suggest or change medicines |
| Verify an existing patient by name and date of birth | Say whether the caller needs surgery |
| Listen to symptoms, show sympathy, ask short follow-up questions | Give treatment advice |
| Flag emergencies and transfer the call to the doctor | Reveal records before verification |
| Save callback requests | Invent clinic information that is not in the approved data |
| Answer in the caller's language (English, Hindi, Odia) | Make promises about appointments that the booking system has not confirmed |

If the answer is not in the approved data, the bot says the doctor's team will call back, and saves a callback.

---

## 4. Architecture in layers

```text
┌──────────────────────────────────────────────────────────────────────┐
│  PHONE PROVIDER   Twilio (testing)  /  Plivo (production in India)   │
│  number, call routing, live audio stream, ringing the doctor         │
└─────────────────────────────┬────────────────────────────────────────┘
                              │ webhooks + WebSocket
┌─────────────────────────────▼────────────────────────────────────────┐
│  ADAPTER LAYER    app/telephony/       translates provider messages  │
│  into neutral events, so nothing else depends on the provider        │
└─────────────────────────────┬────────────────────────────────────────┘
┌─────────────────────────────▼────────────────────────────────────────┐
│  CALL SESSION     app/voice/session.py     one object per live call  │
│  listens, detects sentences, handles interruptions, runs each turn   │
└───────┬───────────────────────┬──────────────────────────┬───────────┘
        │                       │                          │
┌───────▼────────┐   ┌──────────▼───────────┐   ┌──────────▼───────────┐
│ SPEECH         │   │ AGENT (LangGraph)    │   │ SPEECH               │
│ Bodhan STT     │   │ verify, rules, LLM,  │   │ Bodhan TTS           │
│ voice → text   │   │ tools                │   │ text → voice         │
└────────────────┘   └──────────┬───────────┘   └──────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────────┐
│  DATA     PostgreSQL: patients, call_logs, callbacks, call_summaries │
│           + LangGraph checkpoints (call memory)                      │
│           data/*.json: doctor-approved content                       │
└──────────────────────────────────────────────────────────────────────┘
```

Two ideas hold it together:

- **One brain, two doors.** The text test endpoints (`/chat/*`) and the phone calls use exactly the same LangGraph agent.
- **Providers are plug-ins.** Only the adapter and the routes file know whether the call is Twilio or Plivo.

---

## 5. End-to-end call workflow

```text
 Patient dials the clinic number
        │
        ▼
 Provider receives the call ──► POST /voice/<provider>/answer
        │                        reply: "open a two-way audio stream to our server"
        ▼
 Provider opens WebSocket ──► /voice/<provider>/stream     (a CallSession starts)
        │
        ▼
 start event ─► save the call log, find the caller's number
        │
        ▼
 Greeting spoken (pre-generated, so it starts at once)
        │
        ▼
 ┌─────────────── LOOP: every sentence the caller says ─────────────────┐
 │  audio → voice detector finds the end of the sentence                │
 │  → Bodhan speech-to-text                                             │
 │  → LangGraph agent (verify / emergency check / answer / tools)       │
 │  → Bodhan text-to-speech, sentence by sentence                       │
 │  → audio played to the caller (caller can interrupt at any time)     │
 └───────────────┬──────────────────────────────────┬───────────────────┘
                 │ emergency detected               │ caller hangs up
                 ▼                                  ▼
        transfer to the doctor              summary saved + doctor alert
```

### 5.1 One conversation turn in detail

```text
 caller speaks
   └► frames of 20 ms audio (mu-law, 8 kHz)
        └► VoiceActivityDetector: speech starts … silence ≥ 0.8 s = sentence finished
             └► converted to 16 kHz WAV
                  └► Bodhan indic-transcribe → text (language auto-detected)
                       └► LangGraph.invoke(text, call_id)
                            └► reply text
                                 └► split into short pieces ("Dr." is never split)
                                      └► Bodhan indic-speak (pieces made in parallel, played in order)
                                           └► mu-law audio chunks → provider → caller
```

Interruption: if the caller speaks for more than `BARGE_IN_MS` while the bot is talking, the bot's queued audio is cleared immediately and the new sentence is processed.

---

## 6. The agent (LangGraph) workflow

Runs once for every caller message. The call memory is saved in Postgres under the call id, so the bot remembers the whole call, even across a server restart.

```text
 START
   │
   ▼
 lookup_patient   who is calling? (once per call) existing patient or new caller
   │
   ▼
 urgency_check    plain-Python rules on EVERY message, before the LLM
   │
   ├── emergency ─────────► escalate ──► END   (callback saved, doctor alerted, transfer requested)
   │
   ├── existing, not verified yet (fewer than 2 attempts) ─► verify_identity ──► END
   │
   └── everything else ───► assistant ◄──► tools ──► END
```

| Node | What it does |
|---|---|
| `lookup_patient` | normalizes the caller's number and looks it up; if the database fails, the caller is treated as new so nothing private is revealed |
| `urgency_check` | classifies the message as routine, urgent or emergency from `data/emergency_rules.json`; if the check itself fails, it assumes urgent |
| `escalate` | saves an emergency callback, alerts the doctor, sets `action = "transfer"`; it always transfers, even if saving fails |
| `verify_identity` | the LLM extracts a name and date of birth (name in English letters, dates as day/month/year); the name is matched fuzzily and the date exactly; two failures stop verification and a callback is offered |
| `assistant` | the normal conversation; the LLM answers within strict rules or calls the `request_callback` tool; on an LLM error the caller hears an approved fallback line |

### 6.1 Existing and new patients

| | Existing patient | New caller |
|---|---|---|
| How recognised | phone number found in `patients` | number not found (or the database was unreachable) |
| First step | asked for full name and date of birth | greeted; asked for name, date of birth and what happened, one question at a time |
| After verification | can describe symptoms and ask questions | the bot saves a callback request when it has all three details |
| Never | shares anything personal before verifying | creates an official patient record (staff do that) |

### 6.2 Urgency levels

| Level | Examples | Action |
|---|---|---|
| **Emergency** | cannot walk or stand, bone sticking out, heavy bleeding, numbness, road accident | transfer to the doctor immediately |
| **Urgent** | pain getting worse, new swelling, stitches problem | the bot reassures and saves an urgent callback |
| **Routine** | follow-ups, questions, appointments | bot handles it |

The phrase lists are **placeholders until the doctor approves them**.

---

## 7. Emergency and transfer workflow

```text
 caller says an emergency phrase
   │
   ▼
 escalate node ─► callback saved (urgency: emergency) ─► doctor alert 🚨
   │
   ▼
 bot says the "stay on the line" line, then the provider transfers the live call
   │
   ▼
 /voice/<provider>/transfer  ──► rings ESCALATION_PHONE (the doctor), up to 25 s
   │
   ├── answered ──► caller is connected to the doctor ─► call ends normally
   │
   └── not answered
          │
          ▼
        BACKUP_PHONE set? ─ yes ─► rings the backup number
          │ no / not answered
          ▼
        "The doctor is busy, he will call you back shortly. If it is life-threatening,
         contact local emergency services." ─► hang up
         (the callback is already saved and the doctor was alerted again)
```

If the transfer request itself fails, the bot plays the same "doctor is busy" line and the call stays open.

---

## 8. After the call

When the stream closes (the caller hangs up or the call is transferred):

1. The conversation is read from the saved call memory.
2. The LLM writes a 3 to 5 line summary for the doctor (who called, the problem, severity, what was promised, what the doctor must do). If the LLM fails, the last few messages are saved instead.
3. The summary and the final urgency are saved in `call_summaries` and `call_logs`.
4. The doctor is alerted **only if the call matters**: urgent or emergency calls, and new patients.

Alerts are currently written to the server log (`doctor_alerts`). Planned channels: Telegram bot, email with a PDF summary, and optionally WhatsApp later.

---

## 9. Data stored

| Table / store | Content |
|---|---|
| `patients` | full name, phone number (E.164), date of birth |
| `call_logs` | one row per call: id, caller phone, patient id, caller type, urgency, start and end time (metadata only) |
| `callbacks` | callback requests: call id, name, phone, date of birth, issue, urgency, status (pending/done) |
| `call_summaries` | one summary per call with its urgency |
| LangGraph checkpoint tables | the live conversation state per call (created automatically) |
| `data/*.json` | doctor-approved clinic info, emergency phrases and fixed replies |

Transcripts are **not** written to the logs; the code logs only the number of characters spoken.

---

## 10. Languages

- Speech-to-text auto-detects English, Hindi and Odia, and handles code-mixed speech, with the weakest area being sentences that mix all three.
- The bot replies in the caller's main language, in that language's own script, and falls back to English when unsure.
- The text-to-speech language is picked from the script of the reply (Odia script means Odia, Devanagari means Hindi, otherwise English).
- **Fixed lines** (verification, emergency, refusals, greetings) are English only for now. Hindi and Odia versions need the doctor's approval.
- Romanized Hindi or Odia (for example "mere hand mein pain hai") is spoken with the English voice.

---

## 11. Safety principles built into the design

1. **Emergency detection is not left to the LLM.** It is plain, testable Python on a doctor-approved phrase list, and it runs before the LLM on every message.
2. **Approved words only.** Greetings, refusals, transfer lines and error lines come from `data/fixed_replies.json`; clinic facts come from `data/clinic_info.json`.
3. **No medical advice.** Diagnosis, medicines and surgery decisions get a refusal line and a saved question for the doctor.
4. **Identity before information.** A phone number alone never unlocks personal data; name and date of birth are required, with limited attempts.
5. **A failure never silences or crashes a call.** Every step (database, LLM, speech, transfer) has a safe fallback line.
6. **The doctor always hears about important calls**, even if a step fails.
7. **Privacy by default.** Metadata and short summaries are stored, not recordings or transcripts.
8. **Honest identity.** The first words tell the caller it is an AI assistant.

---

## 12. Current status

### Working
- Text agent: verification, natural replies, emergency rules, callbacks, summaries
- Real phone call over Twilio: greeting, speech in and out, interruption, summary, doctor alert in the log
- Speech: Bodhan STT and TTS, sentence-by-sentence speech, greeting pre-generation
- Provider switch between Twilio and Plivo by one `.env` setting
- Test tools: chat, speech scripts, voice samples, call simulator

### Known gaps
| Gap | Note |
|---|---|
| Doctor alerts are log-only | Telegram, email and PDF summaries are the next step |
| Bot cannot read patient records yet | needs the doctor's approved list of what may be read out, then a lookup tool |
| No real appointment booking | the bot saves a callback instead |
| New patients do not always get a saved callback automatically | small fix: save one at the end of the call for new and urgent callers |
| Fixed lines English only | translations must be approved by the doctor |
| No silent-caller timeout or maximum call length | needed before production (providers bill per minute) |
| No dashboard for callbacks | the doctor's team cannot yet view or mark them done |
| Delay of about 3 to 6 seconds per reply | speech is not streamed yet; the voice detector is energy-based |
| Database tables made with `create_all` | move to Alembic migrations |
| Emergency phrases, fees, timings and FAQ are placeholders | must be approved by the doctor |

### Roadmap
1. **Phase 3:** doctor alerts (Telegram + email + PDF), callback list, patient record lookup tool.
2. **Phase 4:** reminder calls: the agent phones patients the day before surgery ("tomorrow is your appointment, how is your leg?"), driven by the doctor's Excel sheet, with confirm/reschedule tools and call retries.
3. **Production:** Plivo India number (India-region account and business KYC), HTTPS hosting (Azure VM), Alembic, secrets management, monitoring, signature validation on, consent and retention policy.

---

## 13. Glossary

| Term | Meaning |
|---|---|
| **STT / TTS** | speech-to-text / text-to-speech |
| **VAD** | voice activity detector: finds where the caller starts and stops speaking |
| **Barge-in** | the caller interrupting the bot while it speaks |
| **Webhook** | a URL the phone provider calls when something happens (a call comes in) |
| **WebSocket stream** | the live two-way audio connection between the provider and this server |
| **Adapter** | the code that translates one provider's messages into the app's neutral format |
| **LangGraph** | the library that runs the agent as a graph of steps with saved memory |
| **mu-law 8 kHz** | the audio format used on phone lines |
| **E.164** | the international phone number format, for example `+919999900001` |
