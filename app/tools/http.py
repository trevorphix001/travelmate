"""Shared async HTTP: 10 s timeout, 3 tries with exponential backoff, per-provider rate limit."""
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.ratelimit import limiter

TIMEOUT = httpx.Timeout(10.0, read=30.0)  # Overpass queries can take ~25 s server-side
_SSL = httpx.create_ssl_context()  # building this per request costs ~100 ms


def _retryable(e: BaseException) -> bool:
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code == 429 or e.response.status_code >= 500
    return isinstance(e, httpx.TransportError)


async def request_once(provider: str, method: str, url: str, **kw: Any) -> Any:
    """Single attempt (callers with their own fallback, e.g. alternate Overpass instances)."""
    await limiter(provider).wait()
    headers = {"User-Agent": get_settings().app_user_agent, **kw.pop("headers", {})}
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=headers, follow_redirects=True, verify=_SSL) as client:
        r = await client.request(method, url, **kw)
        r.raise_for_status()
        return r.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=4),
       retry=retry_if_exception(_retryable), reraise=True)
async def request_json(provider: str, method: str, url: str, **kw: Any) -> Any:
    return await request_once(provider, method, url, **kw)


async def get_json(provider: str, url: str, params: dict | None = None) -> Any:
    return await request_json(provider, "GET", url, params=params)
