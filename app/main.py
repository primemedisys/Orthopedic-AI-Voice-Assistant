"""
app/main.py
───────────
Root FastAPI app for Dr. Pushpak Samal's AI Voice Receptionist.

Systems mounted:
  - Chat (text testing)              → /chat/*
  - Voice (Plivo webhooks + audio)   → /voice/plivo/*

Run:
  uvicorn app.main:app --port 8000 --reload
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.postgres import PostgresSaver # type: ignore
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.agent.graph import build_graph
from app.agent.loader import validate_data_files
from app.speech.factory import close_speech_clients, missing_speech_settings, prewarm_fixed_lines

from app.config import settings
from app.database import (
    Base,
    check_db_connection,
    close_db,
    engine,
    get_db,
    init_db,
)
import app.models  # noqa: F401  (registers all tables so create_all can see them)

from app.routes.v1 import (
    chat, 
    voice_plivo, 
    voice_twilio
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger("voice_agent")


# ==================================================
# STARTUP / SHUTDOWN
# ==================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run startup checks, build the agent, and close every connection on shutdown."""
    logger.info("=" * 50)
    logger.info("🚀 Orthopedic AI Voice Agent starting...")
    logger.info("=" * 50)

    checkpoint_pool: ConnectionPool | None = None

    try:
        # Database (asyncpg pool + SQLAlchemy engine)
        await init_db()
        check_db_connection()
        Base.metadata.create_all(engine)  # creates patients, call_logs, callbacks, call_summaries if missing
        logger.info("✅ Database initialized")

        # Doctor-approved JSON files (fails fast if missing or broken)
        validate_data_files()
        logger.info("✅ Data files loaded")

        # LangGraph agent with a Postgres checkpointer (call memory survives restarts)
        checkpoint_pool = ConnectionPool(
            conninfo=settings.psycopg_url,
            max_size=10,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            open=False,
        )
        checkpoint_pool.open(wait=True, timeout=10)
        checkpointer = PostgresSaver(checkpoint_pool)
        checkpointer.setup()  # creates checkpoint tables on first run
        app.state.graph = build_graph(checkpointer)
        logger.info("✅ Agent graph ready")
        
        # Voice settings: warn (not crash) so the chat endpoints keep working
        missing = settings.missing_voice_settings()
        if missing:
            logger.warning("⚠️ Voice settings still empty: %s", ", ".join(missing))
        
        speech_missing = missing_speech_settings()
        if speech_missing:
            logger.warning("⚠️ Speech settings still empty: %s", ", ".join(speech_missing))
        else:
            logger.info("✅ Speech: STT=%s | TTS=%s | default language=%s", settings.STT_PROVIDER, settings.TTS_PROVIDER, settings.DEFAULT_LANGUAGE)
            app.state.prewarm_task = asyncio.create_task(prewarm_fixed_lines())  # background, saved on disk

    except Exception as e:
        logger.exception("❌ Startup failed: %s", e)
        if checkpoint_pool is not None:
            checkpoint_pool.close()
        await close_db()
        raise

    yield

    # Shutdown: close everything we opened
    checkpoint_pool.close()
    await close_speech_clients()
    await close_db()
    logger.info("👋 Orthopedic AI Voice Agent stopped")


# ==================================================
# APP
# ==================================================

app = FastAPI(
    title="Orthopedic AI Voice Agent",
    description="AI voice receptionist for Dr. Pushpak Samal",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(chat.router)
app.include_router(voice_plivo.router)
app.include_router(voice_twilio.router)

# ==================================================
# ENDPOINTS
# ==================================================


@app.get("/", tags=["Health"])
async def root():
    """Service info and a list of the available endpoints for the active phone provider."""
    provider = settings.TELEPHONY_PROVIDER.lower()
    return {
        "service": "Orthopedic AI Voice Agent",
        "status": "✅ running",
        "systems": {
            "chat_test": {"start": "/chat/start", "message": "/chat/message", "end": "/chat/end"},
            "voice_call": {
                "provider": provider,
                "answer": f"/voice/{provider}/answer",
                "stream": f"/voice/{provider}/stream",
                "transfer": f"/voice/{provider}/transfer",
            },
        },
        "docs": "/docs",
    }
    
@app.get("/health", tags=["Health"])
async def health():
    """Check the database. Returns 200 when healthy and 503 when it is not, so monitors can alert."""
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")

        return {
            "status": "✅ Running",
            "database": "✅ connected",
            "agent": "✅ ready" if getattr(app.state, "graph", None) else "❌ not loaded",
            "docs": "/docs",
        }

    except Exception:
        logger.exception("[Health] ❌ Database health check failed")
        return JSONResponse(
            status_code=503,
            content={
                "status": "❌ Degraded", 
                "database": "❌ disconnected", 
                "docs": "/docs"
            },
        )