"""
app/speech/sarvam/client.py
───────────────────────────
One shared HTTP client for Sarvam. A caller is listening, so on HTTP 429 we retry once
only if Sarvam asks for a very short wait; otherwise we fail fast.
"""

import asyncio

import httpx

from app.config import settings

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """Shared client (reuses connections, so calls are faster)."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=settings.SARVAM_API_BASE_URL.rstrip("/"),
            headers={"api-subscription-key": settings.SARVAM_API_KEY},
            timeout=httpx.Timeout(20.0, connect=5.0),
        )
    return _client


async def close_client() -> None:
    """Close the shared client on shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def post(path: str, **kwargs) -> httpx.Response:
    """POST to Sarvam. Retries once on 429 if the wait is 3 seconds or less."""
    client = get_client()
    response = await client.post(path, **kwargs)
    if response.status_code == 429:
        try:
            wait = float(response.headers.get("retry-after", "1"))
        except ValueError:
            wait = 1.0
        if wait <= 3:
            await asyncio.sleep(max(wait, 0.5))
            response = await client.post(path, **kwargs)
    return response


def describe_error(response: httpx.Response) -> str:
    """Short, safe error text for logs."""
    try:
        body = response.json()
        error = body.get("error", body)
        return str(error.get("message") or error)[:200] if isinstance(error, dict) else str(error)[:200]
    except Exception:
        return response.text[:200]