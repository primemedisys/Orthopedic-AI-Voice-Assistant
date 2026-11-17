# Orthopedic AI Voice Agent: Project Guide

How the project works, how to set it up, how to test it, and where every file lives.

> Doctor: Dr. Pushpak Samal (confirm the exact spelling with the client, because the bot says it aloud).
> Status: working prototype. Chat testing and a real Twilio phone call both work end to end.

---

## 1. What this project is

An AI receptionist that answers the clinic's phone number.

- A patient calls. The phone provider (Twilio now, Plivo for production in India) connects the call to this FastAPI backend over a live audio WebSocket.
- The caller's speech is converted to text (Bodhan speech-to-text), answered by a LangGraph agent, and spoken back (Bodhan text-to-speech).
- Existing patients are verified by name and date of birth. New patients are asked for their details.
- Emergencies are detected with plain Python rules (no LLM) and the call is transferred to the doctor. If he does not answer, the bot says he is busy and a callback is saved.
- When the call ends, a short summary is saved and the doctor is alerted (alerts are log-only for now).

The same agent also runs in a text-only test mode (`/chat/*`), which is the fastest way to test the logic without a phone.

---

## 2. Requirements

| Item | Version / note |
|---|---|
| OS used in development | Windows 10/11, PowerShell |
| Python | 3.11 (3.12 works; on 3.13+ install `audioop-lts`) |
| Docker Desktop | for a local PostgreSQL |
| ngrok | public HTTPS URL for the phone provider |
| OpenAI API key | the LLM (`gpt-4o-mini` by default) |
| Bodhan API keys | one for `indic-transcribe` (STT) and one for `indic-speak` (TTS) |
| Phone provider account | Twilio (test) or Plivo (production in India) |

---

## 3. First-time setup

### 3.1 Virtual environment and packages

```powershell
cd C:\Users\TUF\Freelance\Orthopedic-AI-Voice-Agent
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

`requirements.txt`:

```text
fastapi
uvicorn[standard]
pydantic-settings
python-multipart
asyncpg
sqlalchemy>=2.0
psycopg[binary,pool]
alembic
phonenumbers
httpx
plivo
twilio
audioop-lts; python_version >= "3.13"
langgraph
langgraph-checkpoint-postgres
langchain-core
langchain-openai
```

### 3.2 Database (Docker)

```powershell
docker run --name orthopedic-db -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=orthopedic_voice -p 5433:5432 -v orthopedic_db_data:/var/lib/postgresql/data --restart always -d postgres:16
```

- Port **5433** on your PC maps to 5432 inside the container (avoids clashing with a local Postgres).
- The named volume `orthopedic_db_data` keeps your data when the container is recreated.
- If the app says "connection refused" on port 5433, Docker is not running: open Docker Desktop, then `docker start orthopedic-db`.

### 3.3 Environment file (`.env`)

Create `.env` in the project root. **Never commit it.** Keys live only here, never in `config.py`.

```dotenv
APP_ENV=development
DATABASE_URL=postgresql://postgres:postgres@localhost:5433/orthopedic_voice

# Public HTTPS URL of this app (ngrok now, a domain later). No trailing slash.
PUBLIC_BASE_URL=https://YOUR-NGROK-URL

OPENAI_API_KEY=YOUR_OPENAI_KEY
OPENAI_MODEL=gpt-4o-mini

# Active phone provider: twilio or plivo
TELEPHONY_PROVIDER=twilio

# Twilio
TWILIO_ACCOUNT_SID=YOUR_TWILIO_SID
TWILIO_AUTH_TOKEN=YOUR_TWILIO_AUTH_TOKEN
TWILIO_NUMBER=+1XXXXXXXXXX
TWILIO_VALIDATE_SIGNATURE=false
TWILIO_VOICE=Polly.Aditi
SPEECH_LANGUAGE=en-IN

# Plivo
PLIVO_AUTH_ID=
PLIVO_AUTH_TOKEN=
PLIVO_NUMBER=
PLIVO_VALIDATE_SIGNATURE=false
PLIVO_SPEAK_VOICE=
PLIVO_SPEAK_LANGUAGE=

# Bodhan (one key per model)
BODHAN_API_BASE_URL=https://api.bodhan.ai/v1
BODHAN_TRANSCRIBE_API_KEY=YOUR_STT_KEY
BODHAN_SPEAK_API_KEY=YOUR_TTS_KEY
BODHAN_VOICE=Kavya
BODHAN_TTS_STYLE=
# Leave EMPTY so Bodhan detects English / Hindi / Odia. Set "en" only for English-only tests.
BODHAN_STT_LANGUAGE=

# Doctor transfer (use a DIFFERENT phone from the one that forwards to the bot)
ESCALATION_PHONE=
BACKUP_PHONE=
```

Important rules:

- An **empty** line such as `BODHAN_VOICE=` overrides the default in `config.py` with an empty value. Fill the value or delete the line.
- After changing `.env`, **stop and restart** the server by hand (the auto-reload only watches `.py` files).
- Every time ngrok restarts, the URL changes. Update `PUBLIC_BASE_URL` and the webhook in the provider console.

### 3.4 Doctor-approved data files

The folder `data/` (project root, next to `app/`) holds what the bot may say. Edit these files, not the code. The app refuses to start if one is missing or invalid.

| File | Content |
|---|---|
| `data/clinic_info.json` | address, timings, fees, procedures (for example ACL/MCL surgery cost), FAQs |
| `data/emergency_rules.json` | phrases that mean emergency or urgent (**must be approved by the doctor**) |
| `data/fixed_replies.json` | exact lines the bot says (greeting, verification, refusals, errors); each key has 3 variants and one is picked at random |

Required keys in `fixed_replies.json`: `greeting`, `ask_identity`, `ask_name`, `ask_dob`, `verified`, `verify_retry`, `verify_failed`, `medical_advice_decline`, `emergency_transfer`, `transfer_failed`, `error`, `not_understood`.

Keys in `clinic_info.json` that start with `_` are notes for humans and are hidden from the bot.

### 3.5 Create the tables and a test patient

```powershell
python -c "import app.models; from app.database import Base, engine; Base.metadata.create_all(engine)"
```

Add yourself as a patient so you can test the verified flow (use your real name and date of birth):

```powershell
docker exec -it orthopedic-db psql -U postgres -d orthopedic_voice -c "INSERT INTO patients (full_name, phone_number, date_of_birth) VALUES ('YOUR NAME', '+91XXXXXXXXXX', 'YYYY-MM-DD');"
```

`create_all` is for local testing only. Use Alembic migrations before a real deployment. The LangGraph checkpoint tables are created automatically at startup.

### 3.6 Run the server

```powershell
uvicorn app.main:app --port 8000 --reload
```

A healthy start looks like this:

```text
✅ Connected to PostgreSQL
✅ Database initialized
✅ Data files loaded
✅ Agent graph ready
✅ Voice settings loaded         (or a ⚠️ line naming what is still empty)
✅ [TTS] Greetings pre-generated (a few seconds later)
```

Open `http://localhost:8000/docs` to see all endpoints. `/health` returns 503 if the database is down.

---

## 4. Configuration reference

| Setting | Purpose |
|---|---|
| `DATABASE_URL` | Postgres URL. Both `postgresql://` and `postgresql+psycopg://` work |
| `PUBLIC_BASE_URL` | Public HTTPS URL used in webhook XML and the audio WebSocket URL |
| `TELEPHONY_PROVIDER` | `twilio` or `plivo`. Chooses which voice routes are mounted |
| `*_VALIDATE_SIGNATURE` | Verify that webhooks really come from the provider. Keep `false` while testing behind ngrok, `true` in production |
| `BODHAN_VOICE` / `BODHAN_TTS_STYLE` | Exact, case-sensitive voice name and optional style |
| `BODHAN_STT_LANGUAGE` | Empty = auto-detect English/Hindi/Odia |
| `VAD_RMS_THRESHOLD` | Voice detector sensitivity. Lower (for example 300) if the bot does not hear you |
| `VAD_END_SILENCE_MS` | Silence that ends a sentence (800 default; 650 answers faster) |
| `VAD_MIN_SPEECH_MS` | Shorter sounds are ignored as noise |
| `VAD_MAX_UTTERANCE_S` | Longest sentence sent to STT (Bodhan limit is 30 s) |
| `BARGE_IN_MS` | How long the caller must speak to interrupt the bot (raise to 700 on speakerphone) |
| `ESCALATION_PHONE` / `BACKUP_PHONE` | Doctor and backup numbers for emergency transfer |
| `TRANSFER_RING_SECONDS` | How long to ring each number |

---

## 5. Testing: five levels, cheapest first

### Level 1: chat (no phone, no speech)

In Swagger at `http://localhost:8000/docs`:

1. `POST /chat/start` with `{ "phone": "+91XXXXXXXXXX" }`. Copy the `call_id`.
2. `POST /chat/message` with `call_id`, `phone` and `text`. Repeat for each caller message.
3. `POST /chat/end` with `call_id` to save the summary.

| Try | Expected |
|---|---|
| "Hi, I want to ask about my knee" (existing patient) | bot asks for name and date of birth |
| "I am NAME, born 12 March 1990" | `verified: true` |
| "Do I need surgery?" | polite refusal, callback saved |
| "I fell down and I cant walk" | `action: transfer`, `urgency: emergency`, `🚨 EMERGENCY` log |
| a phone number not in the database | treated as a new patient, details collected |
| wrong date of birth twice | verification stops, callback offered |

Check saved data:

```powershell
docker exec -it orthopedic-db psql -U postgres -d orthopedic_voice -c "SELECT id, name, issue, urgency FROM callbacks;"
docker exec -it orthopedic-db psql -U postgres -d orthopedic_voice -c "SELECT call_id, urgency, summary FROM call_summaries;"
```

### Level 2: Bodhan speech alone

```powershell
python -m app.scripts.test_speech        # text to speech, then back to text, in English, Hindi, Odia
python -m app.scripts.make_voices        # saves WAV files into voice_samples/ (original and phone quality)
python -m app.scripts.make_voices Suhani # try another voice
python -m app.scripts.test_voices        # compares several voices and styles
python -m app.scripts.test_languages --chat   # language round trip; --chat also checks the agent's reply language
```

Play the WAV files to judge the voice. Pick the best `BODHAN_VOICE` and `BODHAN_TTS_STYLE`.

### Level 3: simulated phone call (no provider account)

`test_speech` also creates `caller_1.wav`, `caller_2.wav` and `caller_3.wav`. With the server running:

```powershell
python -m app.scripts.simulate_call twilio +91XXXXXXXXXX caller_1.wav caller_2.wav caller_3.wav
```

It pretends to be the provider, speaks the files to your server, and saves the bot's replies as `sim_greeting.wav` and `sim_reply_N.wav`. Use `plivo` instead of `twilio` to test the Plivo message format.

### Level 4: real phone call (Twilio)

1. Run `ngrok http 8000` and set `PUBLIC_BASE_URL` to the HTTPS URL. Restart the server.
2. Check `https://YOUR-NGROK-URL/health` in a browser.
3. In the Twilio console open your number, go to **Voice** configuration, and set **A call comes in** to **Webhook**, `https://YOUR-NGROK-URL/voice/twilio/answer`, **HTTP POST**. (This is the **Voice** tab, not the Messaging one.)
4. Call the Twilio number. If the account is a trial, the caller must be a verified caller ID.
5. The log should show:

```text
📞 [Voice] Incoming call CAxxxx
✅ [Voice] Stream started for call CAxxxx
```

Then you hear the greeting. After you hang up, the summary is saved and a doctor alert is logged.

If something fails, open `http://127.0.0.1:4040` (ngrok inspector). It shows every request the provider sent and your server's reply.

### Level 5: forward your own SIM to the bot (optional)

Dial on the SIM that should forward (Airtel codes; check your carrier's own steps):

| Action | Code |
|---|---|
| Forward all calls | `**21*+1XXXXXXXXXX#` (your Twilio number) |
| Check status | `*#21#` |
| Stop forwarding | `##21#` |
| Reset every rule | `##002#` |

Never use the forwarding SIM as `ESCALATION_PHONE` (an emergency would ring a number that forwards back to the bot). Forwarding to an international number may need to be enabled by the carrier and is billed to that SIM's plan. Cancel it when you finish testing.

---

## 6. Daily commands

```powershell
# Start Docker Desktop first, then:
docker start orthopedic-db
cd C:\Users\TUF\Freelance\Orthopedic-AI-Voice-Agent
.venv\Scripts\activate
uvicorn app.main:app --port 8000 --reload

# In another terminal:
ngrok http 8000          # then update PUBLIC_BASE_URL and the provider webhook
```

---

## 7. File structure

```text
Orthopedic-AI-Voice-Agent/
├── app/
│   ├── main.py                    # app start, startup checks, mounts routes, /health
│   ├── config.py                  # all settings, read from .env
│   ├── database.py                # asyncpg pool + SQLAlchemy session
│   │
│   ├── agent/                     # the LangGraph brain (same agent for chat and phone)
│   │   ├── state.py               #   per-call memory (caller, verified, urgency, action...)
│   │   ├── llm.py                 #   shared LLM client
│   │   ├── loader.py              #   loads data/*.json; pick_reply() rotates variants
│   │   ├── rules.py               #   emergency detection in plain Python (no LLM)
│   │   ├── prompts.py             #   system prompt (tone, language, safety rules)
│   │   ├── tools.py               #   request_callback tool
│   │   ├── nodes.py               #   graph steps: lookup, urgency, verify, escalate, assistant
│   │   └── graph.py               #   wires the nodes together
│   │
│   ├── models/                    # database tables
│   │   ├── patient.py             #   patients (name, phone, date of birth)
│   │   ├── call_logs.py           #   one row per call (metadata only)
│   │   ├── callback.py            #   callback requests for the doctor's team
│   │   └── call_summary.py        #   AI-written summary of each call
│   │
│   ├── services/
│   │   ├── patient_service.py     #   phone normalizing, lookup, name + DOB check
│   │   ├── call_service.py        #   call logs and callbacks
│   │   ├── summary_service.py     #   end-of-call summary + doctor alert
│   │   └── notification_service.py#   doctor alerts (log only for now)
│   │
│   ├── routes/v1/
│   │   ├── chat.py                #   text test endpoints (/chat/*)
│   │   ├── voice.py               #   Plivo webhooks + audio WebSocket
│   │   └── voice_twilio.py        #   Twilio webhooks + audio WebSocket
│   │
│   ├── telephony/                 # the only place that knows the phone providers
│   │   ├── base.py                #   adapter contract, StreamEvent, audio constants
│   │   ├── plivo_adapter.py       #   Plivo XML, messages, transfer, signature
│   │   ├── twilio_adapter.py      #   Twilio TwiML, messages, transfer, signature
│   │   └── __init__.py            #   get_adapter()
│   │
│   ├── speech/                    # Bodhan and audio handling
│   │   ├── client.py              #   shared HTTP client, 429 retry, error text
│   │   ├── audio_utils.py         #   mu-law / PCM / WAV conversion, voice detector
│   │   ├── bodhan_stt.py          #   speech to text
│   │   └── bodhan_tts.py          #   text to speech, sentence splitting, cache, voice check
│   │
│   ├── voice/
│   │   └── session.py             #   one live call: listen, think, speak, interrupt, transfer
│   │
│   └── scripts/                   # developer tools (not used by the running app)
│       ├── import_patients.py     #   Excel to Postgres
│       ├── test_speech.py         #   Bodhan round trip + creates caller_N.wav
│       ├── make_voices.py         #   saves voice samples as WAV
│       ├── test_voices.py         #   compares voices and styles
│       ├── test_languages.py      #   English / Hindi / Odia / mixed round trip
│       └── simulate_call.py       #   fake provider call, no account needed
│
├── data/                          # doctor-approved content
│   ├── clinic_info.json
│   ├── emergency_rules.json
│   └── fixed_replies.json
├── migrations/                    # Alembic (when you leave create_all)
├── .env                           # secrets (never commit)
├── .env.example
├── .gitignore                     # include .env, *.wav, voice_samples/
├── docker-compose.yaml
├── Dockerfile
├── requirements.txt
└── README.md
```

Generated test files such as `tts_*.wav`, `caller_*.wav`, `sim_*.wav` and `voice_samples/` are safe to delete.

---

## 8. Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /` | service info |
| `GET /health` | database check (503 if down) |
| `POST /chat/start`, `/chat/message`, `/chat/end` | text test mode |
| `POST /voice/<provider>/answer` | provider "call comes in" webhook, returns the stream XML |
| `WS /voice/<provider>/stream` | live two-way audio, one `CallSession` per call |
| `POST /voice/<provider>/stream-status` | stream started/stopped/error (logged) |
| `POST /voice/<provider>/transfer` | XML that rings the doctor (attempt 1) or backup (attempt 2) |
| `POST /voice/<provider>/transfer-status` | result of ringing: connected, try backup, or play the "doctor busy" line |

`<provider>` is `twilio` or `plivo`. Only the active one (`TELEPHONY_PROVIDER`) is mounted.

---

## 9. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `connection refused` on port 5433 at startup | Docker is not running: start Docker Desktop, then `docker start orthopedic-db` |
| `relation "patients" does not exist` | tables missing: run the `create_all` command (section 3.5) |
| `Data file missing` at startup | `data/` folder is not in the project root, or a JSON file is missing |
| `/docs` shows the wrong provider's routes | `.env` changed but the server was not restarted; check `TELEPHONY_PROVIDER` |
| Warning about an empty voice setting | read the named setting in the warning and fill it in `.env` |
| Call: "application error" or instant hang-up | wrong webhook URL, or ngrok not running; check the ngrok inspector |
| `403 Invalid ... signature` in the log | set `*_VALIDATE_SIGNATURE=false` while testing |
| Call connects but silence | look for `❌ [TTS]` in the log: wrong key or voice name (names are case-sensitive) |
| Bot does not hear you | lower `VAD_RMS_THRESHOLD` (try 300) |
| Bot cuts itself off | raise `BARGE_IN_MS` (try 700) or avoid speakerphone |
| Hindi/Odia misunderstood | `BODHAN_STT_LANGUAGE` must be empty |
| Trial account rejects the call (Twilio error 21264) | the caller is not a verified caller ID |
| Transfer to the doctor fails | enable the doctor's country in the provider's geo permissions; on a trial the number must be verified |

---

## 10. Security checklist

- Keys only in `.env`. Rotate any key that was ever pasted into a chat, a screenshot or a committed file.
- Do not log transcripts (the code logs only the number of characters).
- Turn on `*_VALIDATE_SIGNATURE` before real patients call.
- Disable the `/chat/*` test routes in production, or protect them.
- Decide a retention period for summaries and call logs, and encrypt the production database.
- The first words of every call must tell the caller it is an AI assistant and that the call may be recorded or used to help the doctor (the greeting does this).
