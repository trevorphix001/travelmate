import asyncio
import time

from app.core.ratelimit import RateLimiter


async def test_min_interval_enforced():
    lim = RateLimiter(0.05)
    t0 = time.monotonic()
    await asyncio.gather(*(lim.wait() for _ in range(4)))
    assert time.monotonic() - t0 >= 0.15 - 0.01  # 3 gaps of 50 ms


async def test_zero_interval_no_wait():
    lim = RateLimiter(0)
    t0 = time.monotonic()
    for _ in range(10):
        await lim.wait()
    assert time.monotonic() - t0 < 0.05
