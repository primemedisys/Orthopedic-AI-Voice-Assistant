"""
app/speech/client.py
────────────────────
One shared HTTP client for all Bodhan calls (reuses connections, so calls are faster),
plus a 429 retry helper and safe error text for logs.
"""

import asyncio
import httpx

from app.config import settings

_client: httpx.AsyncClient | None = None

def get_http_client() -> httpx.AsyncClient:
    """Return the shared client, creating it on first use."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=settings.BODHAN_API_BASE_URL.rstrip("/"),
            timeout=httpx.Timeout(20.0, connect=5.0),
        )
    return _client

async def close_http_client() -> None:
    """Close the shared client on shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None

async def post_with_retry(path: str, **kwargs) -> httpx.Response:
    """Retry Bodhan 429 responses with progressively longer delays."""
    client = get_http_client()

    delays = [2, 5, 10]

    for attempt in range(len(delays) + 1):
        response = await client.post(path, **kwargs)

        if response.status_code != 429:
            return response

        if attempt == len(delays):
            return response

        try:
            retry_after = float(response.headers.get("retry-after", "0"))
        except (TypeError, ValueError):
            retry_after = 0

        wait = max(retry_after, delays[attempt])

        # Avoid waiting indefinitely if the provider sends an unusually large value.
        wait = min(wait, 30)

        import logging
        logging.warning(
            "Bodhan returned 429. Retry %s/%s in %s seconds.",
            attempt + 1,
            len(delays),
            wait,
        )

        await asyncio.sleep(wait)

    return response

def describe_error(response: httpx.Response) -> str:
    """Short error text from Bodhan's {"error": {"message", "code", "request_id"}} body (safe to log)."""
    try:
        error = response.json().get("error", {})
        return (f"{error.get('code', '?')}: {str(error.get('message', ''))[:150]} "
                f"(request {error.get('request_id', '-')})")
    except Exception:
        return response.text[:150]