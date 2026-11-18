"""Single entry point for every cached external call (geo tools, weather, LLM).

Backed by the Postgres api_cache table; when no database is configured it falls back to an
in-process dict so the app and scripts still work (cache just doesn't survive restarts).
"""
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.db import repo

log = logging.getLogger(__name__)


class CacheStore(Protocol):
    async def get(self, key: str, allow_expired: bool = False) -> Any | None: ...
    async def put(self, key: str, provider: str, value: Any, expires_at: datetime) -> None: ...


class RepoStore:
    """Postgres store with an in-memory fallback when the DB is missing or errors."""

    def __init__(self) -> None:
        self._mem: dict[str, tuple[float, Any]] = {}

    async def get(self, key: str, allow_expired: bool = False) -> Any | None:
        try:
            val = await repo.cache_get(key, allow_expired)
            if val is not None:
                return val
        except Exception as e:  # noqa: BLE001 - a broken cache must never break planning
            log.warning("cache read failed: %s", e)
        hit = self._mem.get(key)
        return hit[1] if hit and (allow_expired or hit[0] > time.time()) else None

    async def put(self, key: str, provider: str, value: Any, expires_at: datetime) -> None:
        self._mem[key] = (expires_at.timestamp(), value)
        try:
            await repo.cache_put(key, provider, value, expires_at)
        except Exception as e:  # noqa: BLE001
            log.warning("cache write failed: %s", e)


_store: CacheStore = RepoStore()


def set_store(store: CacheStore) -> None:
    """Swap the backing store (tests use an in-memory fake)."""
    global _store
    _store = store


def make_key(provider: str, key_parts: dict) -> str:
    raw = provider + "|" + json.dumps(key_parts, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


async def get_or_fetch(
    provider: str,
    key_parts: dict,
    fetch: Callable[[], Awaitable[Any]],
    ttl: timedelta,
) -> Any:
    key = make_key(provider, key_parts)
    cached = await _store.get(key)
    if cached is not None:
        return cached
    value = await fetch()
    if value is not None:
        await _store.put(key, provider, value, datetime.now(UTC) + ttl)
    return value


async def lookup(provider: str, key_parts: dict) -> Any | None:
    return await _store.get(make_key(provider, key_parts))


async def lookup_stale(provider: str, key_parts: dict) -> Any | None:
    """Last stored value even if expired: used only when the live service is unavailable."""
    return await _store.get(make_key(provider, key_parts), allow_expired=True)


async def store(provider: str, key_parts: dict, value: Any, ttl: timedelta) -> None:
    await _store.put(make_key(provider, key_parts), provider, value, datetime.now(UTC) + ttl)
