# Orthopedic-AI-Voice-Agent

An AI voice receptionist prototype for Dr. Pushpak Samal's orthopedic clinic, built with Python, FastAPI, LangGraph, and PostgreSQL. Twilio phone-call integration is planned (Phase 2).

## Features

- Look up callers by phone number (existing vs new patient)
- Verify existing patients by full name and date of birth before sharing anything personal
- Hold a natural conversation, with doctor-approved clinic information only
- Detect emergencies with rule-based checks (no LLM) and flag the call for transfer to the doctor
- Save callback requests and short call summaries in PostgreSQL
- Alert the doctor about urgent calls and new patients (currently logged in the terminal; WhatsApp/SMS planned)
- Keep call memory in a Postgres checkpointer, so a restart does not lose a live call

> **Medical safety:** This agent is for receptionist and administrative tasks only. It must not diagnose conditions, prescribe medication, or say whether surgery is needed. Emergency rules and clinic answers must be approved by the doctor. Use synthetic patient data during development.

## How a call flows

```
START -> lookup_patient -> urgency_check -+-> escalate ---------> END    (emergency: callback + doctor alert + transfer)
                                          +-> verify_identity --> END    (existing patient, not verified yet)
                                          +-> assistant <-> tools -> END (normal conversation)
```

## Requirements

- Python 3.11
- Docker (for a local PostgreSQL) or any PostgreSQL 14+
- OpenAI API key
- Twilio credentials (Phase 2, when phone integration is enabled)

## Local Setup (Windows PowerShell)

Run these commands from the project root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

### 1. Start a temporary PostgreSQL with Docker

```powershell
docker run --name orthopedic-db -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=orthopedic_voice -p 5433:5432 -d postgres:16
```

### 2. Configure `.env`

```dotenv
APP_ENV=development
DATABASE_URL=postgresql://postgres:postgres@localhost:5433/orthopedic_voice
PUBLIC_BASE_URL=http://localhost:8000
TELEPHONY_PROVIDER=twilio
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_VALIDATE_SIGNATURE=true
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
ESCALATION_PHONE=
BACKUP_PHONE=
```

Never commit `.env`, API keys, database passwords, or telephony tokens.

### 3. Create the tables and a test patient

```powershell
python -c "import app.models; from app.database import Base, engine; Base.metadata.create_all(engine)"

docker exec -it orthopedic-db psql -U postgres -d orthopedic_voice -c "INSERT INTO patients (full_name, phone_number, date_of_birth) VALUES ('Rahul Sharma', '+919999900001', '1990-03-12');"
```

Table creation with `create_all` is for local testing only. Use Alembic migrations for a real database. The LangGraph checkpoint tables are created automatically on startup.

### 4. Doctor-approved data files

The `data/` folder (project root) holds the content the bot may use. Edit these files, not the code:

| File | Purpose |
|---|---|
| `data/clinic_info.json` | Address, timings, fees, FAQs |
| `data/emergency_rules.json` | Emergency and urgent phrases (must be approved by the doctor) |
| `data/fixed_replies.json` | Exact lines the bot says (greeting, verification, refusals, errors) |

The app will not start if one of these files is missing or invalid.

## Run the API

```powershell
uvicorn app.main:app --reload --port 8000
```

Startup should log `✅ Database initialized`, `✅ Data files loaded` and `✅ Agent graph ready`.

- API root: http://127.0.0.1:8000/
- Health check: http://127.0.0.1:8000/health (returns 503 if the database is down)
- Interactive API docs: http://127.0.0.1:8000/docs

## Test the agent in chat (no phone needed)

Use Swagger at `/docs`:

1. `POST /chat/start` with `{ "phone": "+919999900001" }`, then copy the `call_id`.
2. `POST /chat/message` with `call_id`, `phone` and `text`. Repeat for each caller message.
3. `POST /chat/end` with `call_id` to save the summary and alert the doctor if needed.

Scenarios to try:

| Scenario | What to send | Expected |
|---|---|---|
| Existing patient | Ask about your knee, then "I am Rahul Sharma, born 12 March 1990" | Bot asks for name and DOB, then `verified: true` |
| Medical advice | "Do I need surgery?" | Bot declines and saves a callback |
| Emergency | "I fell down and I cant walk" | `action: transfer`, `urgency: emergency`, `🚨 EMERGENCY` log |
| New patient | Use a number not in the database | Bot collects name, DOB and issue, then saves a callback |
| Wrong details | Wrong DOB twice | Bot stops verifying and offers a callback |

Check the saved data:

```powershell
docker exec -it orthopedic-db psql -U postgres -d orthopedic_voice -c "SELECT id, name, issue, urgency FROM callbacks;"
docker exec -it orthopedic-db psql -U postgres -d orthopedic_voice -c "SELECT call_id, urgency, summary FROM call_summaries;"
```

Useful Docker commands:

```powershell
docker stop orthopedic-db      # pause
docker start orthopedic-db     # resume (data is kept)
docker rm -f orthopedic-db     # delete with all data
```

## Project Structure

```text
app/
├── agent/       # LangGraph state, nodes, graph, rules, prompts, tools, data loader
├── docs/        # Project documentation
├── models/      # SQLAlchemy models (patients, call logs, callbacks, call summaries)
├── routes/      # API routes (v1/chat.py for text testing; voice webhooks in Phase 2)
├── scripts/     # Development scripts (e.g. Excel patient import)
├── services/    # Patient, call, summary and notification logic
├── utils/       # Shared helpers
├── config.py    # Application settings (.env)
├── database.py  # asyncpg pool + SQLAlchemy session
└── main.py      # FastAPI entry point, startup checks, health check

data/            # Doctor-approved JSON content (clinic info, emergency rules, fixed replies)
.env             # Local secrets (do not commit)
.env.example     # Environment-variable template
requirements.txt # Python dependencies
Dockerfile
docker-compose.yaml
```

## Development Notes

- Emergency detection is plain Python in `app/agent/rules.py`. The LLM never decides emergencies alone.
- Spoken replies must not contain emojis. Emojis are used only in logs, doctor alerts and API messages.
- A database or LLM failure must never crash a live call. The caller hears an approved fallback line.
- Do not claim an appointment is booked unless the booking system confirms it.
- Test urgent-call routing and fallback behavior before connecting a real clinic phone number.

## Roadmap

- [x] Phase 1: text agent with verification, emergency rules, callbacks and summaries
- [ ] Phase 2: Twilio webhooks, doctor call transfer, "doctor busy" fallback, WhatsApp/SMS alerts, PDF summaries
- [ ] Phase 3: natural streaming voice (speech-to-text, LLM, text-to-speech)
- [ ] Production: Alembic migrations, hosting, access control, data retention policy

## Status

Active prototype. Phase 1 (chat) works end to end. Phone integration and natural voice are still under development.