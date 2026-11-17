"""
app/routes/health.py
────────────────────
Health check for the server / Docker / uptime monitors.
"""

import logging

from fastapi import APIRouter, HTTPException

from app.database import check_db_connection

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    """Return OK if the app and Postgres are up, or 503 if the database is unreachable."""
    try:
        check_db_connection()
    except Exception:
        logger.exception("❌ [Health] Database unreachable")
        raise HTTPException(status_code=503, detail="❌ Database unreachable")
    return {"status": "success", "message": "✅ Service is healthy"}