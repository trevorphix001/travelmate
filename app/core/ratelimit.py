"""Per-provider minimum interval between outgoing requests (process-wide)."""
import asyncio
import time

PRESETS: dict[str, float] = {
    "nominatim": 1.1, "overpass": 1.0, "osrm": 0.5, "open_meteo": 0.2, "wikipedia": 0.1,
}


class RateLimiter:
    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self._last + self.min_interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = time.monotonic()


_limiters: dict[str, RateLimiter] = {}


def limiter(provider: str) -> RateLimiter:
    if provider not in _limiters:
        _limiters[provider] = RateLimiter(PRESETS.get(provider, 0.0))
    return _limiters[provider]
