"""Supabase JWT verification. Asymmetric keys via the project's JWKS endpoint (default for current
Supabase projects); HS256 with SUPABASE_JWT_SECRET for legacy projects."""
import asyncio
import logging
from functools import lru_cache

import jwt
from fastapi import Request

from app.core.config import get_settings
from app.core.errors import AppError

log = logging.getLogger(__name__)
AUDIENCE = "authenticated"


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    url = get_settings().supabase_url.rstrip("/") + "/auth/v1/.well-known/jwks.json"
    return jwt.PyJWKClient(url, cache_keys=True, timeout=10)


def _unauthorized(detail: str) -> AppError:
    return AppError("unauthorized", "Sign-in required", detail, 401)


async def verify_token(token: str) -> str:
    """Return the user id (sub) of a valid Supabase access token, else raise 401."""
    s = get_settings()
    try:
        alg = jwt.get_unverified_header(token).get("alg", "")
        if alg == "HS256":
            if not s.supabase_jwt_secret:
                raise _unauthorized("HS256 tokens need SUPABASE_JWT_SECRET on the server")
            key = s.supabase_jwt_secret.get_secret_value()
        else:
            if not s.supabase_url:
                raise _unauthorized("SUPABASE_URL is not configured")
            key = (await asyncio.to_thread(_jwks_client().get_signing_key_from_jwt, token)).key
        claims = jwt.decode(token, key, algorithms=[alg], audience=AUDIENCE, options={"require": ["exp", "sub"]})
    except jwt.ExpiredSignatureError as e:
        raise _unauthorized("Token expired") from e
    except jwt.PyJWTError as e:
        raise _unauthorized("Invalid token") from e
    return claims["sub"]


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else None


async def optional_user(request: Request) -> str | None:
    token = _bearer(request)
    return await verify_token(token) if token else None


async def required_user(request: Request) -> str:
    token = _bearer(request)
    if not token:
        raise _unauthorized("Missing bearer token")
    return await verify_token(token)
