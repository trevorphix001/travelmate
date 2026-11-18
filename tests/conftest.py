import json
import time
from pathlib import Path
from typing import Any

import pytest
from tenacity import wait_none

from app.core import cache, ratelimit
from app.tools.http import request_json

request_json.retry.wait = wait_none()  # no backoff sleeps in tests

FIXTURES = Path(__file__).parent / "fixtures"


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict[str, tuple[float, Any]] = {}
        self.puts = 0

    async def get(self, key, allow_expired=False):
        hit = self.data.get(key)
        return hit[1] if hit and (allow_expired or hit[0] > time.time()) else None

    async def put(self, key, provider, value, expires_at):
        self.puts += 1
        self.data[key] = (expires_at.timestamp(), value)


@pytest.fixture(autouse=True)
def no_local_keys(monkeypatch):
    """Tests never use keys from the developer's .env; tests that need one set it explicitly."""
    from app.core.config import Settings
    from app.tools import geoapify_places

    monkeypatch.setattr(geoapify_places, "get_settings", lambda: Settings(_env_file=None))


@pytest.fixture(autouse=True)
def mem_cache():
    """Every test gets a fresh in-memory cache and no rate-limit sleeps."""
    store = MemoryStore()
    cache.set_store(store)
    saved = dict(ratelimit.PRESETS)
    for k in ratelimit.PRESETS:
        ratelimit.PRESETS[k] = 0.0
    ratelimit._limiters.clear()
    yield store
    ratelimit.PRESETS.update(saved)
    ratelimit._limiters.clear()


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def save_fixture(name: str, data: Any) -> None:
    FIXTURES.mkdir(exist_ok=True)
    (FIXTURES / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
