from datetime import timedelta

from app.core import cache


async def test_fetch_then_hit(mem_cache):
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        return {"v": calls}

    a = await cache.get_or_fetch("p", {"q": 1, "z": 2}, fetch, timedelta(hours=1))
    b = await cache.get_or_fetch("p", {"z": 2, "q": 1}, fetch, timedelta(hours=1))  # key order irrelevant
    assert a == b == {"v": 1} and calls == 1


async def test_expired_refetches(mem_cache):
    n = iter([1, 2])

    async def fetch():
        return {"v": next(n)}

    await cache.get_or_fetch("p", {"q": 1}, fetch, timedelta(seconds=-1))
    assert (await cache.get_or_fetch("p", {"q": 1}, fetch, timedelta(hours=1)))["v"] == 2


async def test_none_not_cached(mem_cache):
    async def fetch():
        return None

    await cache.get_or_fetch("p", {"q": 1}, fetch, timedelta(hours=1))
    assert mem_cache.puts == 0


def test_key_depends_on_provider():
    assert cache.make_key("a", {"x": 1}) != cache.make_key("b", {"x": 1})
