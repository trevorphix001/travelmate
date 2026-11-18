"""In-memory per-IP rate limit for plan generation and a request body size cap."""
import time
from collections import defaultdict, deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.core.errors import AppError

MAX_BODY_BYTES = 4096
SAVE_MAX_BYTES = 512 * 1024  # POST /plans/save carries a whole trip; everything else stays small
_hits: dict[str, deque[float]] = defaultdict(deque)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


async def plan_rate_limit(request: Request) -> None:
    s = get_settings()
    now = time.time()
    q = _hits[client_ip(request)]
    while q and q[0] < now - 86400:
        q.popleft()
    last_minute = sum(1 for t in q if t > now - 60)
    if last_minute >= s.plans_per_minute or len(q) >= s.plans_per_day:
        raise AppError("rate_limited", "Too many plan requests",
                       f"Limit is {s.plans_per_minute}/minute and {s.plans_per_day}/day per IP", 429)
    q.append(now)


def reset() -> None:
    _hits.clear()


class BodySizeLimit(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        size = request.headers.get("content-length")
        cap = SAVE_MAX_BYTES if request.url.path.endswith("/plans/save") else MAX_BODY_BYTES
        if size and size.isdigit() and int(size) > cap:
            return JSONResponse({"type": "payload_too_large", "title": "Request body too large",
                                 "detail": f"Max {cap} bytes"}, status_code=413,
                                media_type="application/problem+json")
        return await call_next(request)
