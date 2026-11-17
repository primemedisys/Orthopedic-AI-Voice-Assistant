"""
app/database.py
───────────────
Database layer for the Orthopedic AI Voice Agent.

Two connections to the same PostgreSQL database:
  1) asyncpg pool  → init_db() / get_db() / close_db()  (health checks, async routes)
  2) SQLAlchemy    → Base / get_session()   (models, services, agent nodes, Alembic)
"""

import logging
from contextlib import contextmanager

import asyncpg
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

logger = logging.getLogger("voice.database")


# ==================================================
# ASYNCPG POOL
# ==================================================

_pool: asyncpg.Pool | None = None


async def init_db() -> None:
    """Create the asyncpg pool and verify it with SELECT 1. Raises RuntimeError if it fails."""
    global _pool

    if _pool is not None:  # already initialized, nothing to do
        return

    try:
        _pool = await asyncpg.create_pool(
            dsn=settings.psycopg_url,  # asyncpg needs plain "postgresql://"
            min_size=1,
            max_size=5,
            timeout=10,                # max seconds to open a connection
            command_timeout=10,        # max seconds for one query
        )
        async with _pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        logger.info("✅ Connected to PostgreSQL")

    except Exception as e:
        if _pool is not None:
            await _pool.close()
            _pool = None
        logger.exception("❌ PostgreSQL connection failed")
        raise RuntimeError("Database initialization failed") from e


async def get_db() -> asyncpg.Pool:
    """Return the asyncpg pool. Raises RuntimeError if init_db() has not run."""
    if _pool is None:
        raise RuntimeError("❌ Database not initialized. Call init_db() first.")
    return _pool


async def close_db() -> None:
    """Close the asyncpg pool on shutdown."""
    global _pool

    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("👋 PostgreSQL pool closed")


# ==================================================
# SQLALCHEMY (models, services, agent nodes)
# ==================================================

engine = create_engine(settings.sqlalchemy_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """Parent class of all models."""


def check_db_connection() -> None:
    """Run SELECT 1 through SQLAlchemy to confirm Postgres is reachable. Raises if it is not."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


@contextmanager
def get_session():
    """Yield a DB session: commit on success, rollback + log + re-raise on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("❌ [Database] Transaction failed and was rolled back")
        raise
    finally:
        session.close()