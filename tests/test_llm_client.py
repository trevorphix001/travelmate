import json

import httpx2
import pytest
from pydantic import BaseModel

from app.core.errors import AppError
from app.llm import client
from app.llm.providers import Provider

NV = "https://nv.test/v1/chat/completions"
GQ = "https://gq.test/v1/chat/completions"


class Out(BaseModel):
    ok: bool
    n: int


def completion(content: str) -> dict:
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


class Router:
    """Minimal respx stand-in for httpx2: url -> queue of responses (last one repeats) or exceptions."""

    def __init__(self) -> None:
        self.routes: dict[str, list] = {}
        self.calls: dict[str, list[httpx2.Request]] = {}

    def add(self, url: str, *items) -> None:
        self.routes[url] = list(items)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        self.calls.setdefault(url, []).append(request)
        q = self.routes[url]
        item = q.pop(0) if len(q) > 1 else q[0]
        if isinstance(item, Exception):
            raise item
        status, body = item
        return httpx2.Response(status, json=body)


@pytest.fixture
def router(monkeypatch):
    r = Router()
    monkeypatch.setattr(client, "HTTP_CLIENT", httpx2.AsyncClient(transport=httpx2.MockTransport(r)))
    return r


def ok(content: str):
    return (200, completion(content))


@pytest.fixture(autouse=True)
def providers(monkeypatch):
    ps = {"nvidia": Provider("nvidia", "https://nv.test/v1", "k1", {"planner": "big", "extract": "small"}),
          "groq": Provider("groq", "https://gq.test/v1", "k2", {"planner": "g-big", "extract": "g-small"})}
    monkeypatch.setattr(client, "build_providers", lambda: ps)
    monkeypatch.setattr(client, "provider_order", lambda: ["nvidia", "groq"])


async def call():
    return await client.complete_json("extract", Out, "sys", "user", max_tokens=50)


async def test_success_and_metrics(router):
    router.add(NV, ok('{"ok": true, "n": 1}'))
    out, m = await call()
    assert out == Out(ok=True, n=1) and m.provider == "nvidia" and m.model == "small"
    assert (m.prompt_tokens, m.completion_tokens, m.llm_calls, m.fallback_used) == (10, 5, 1, False)
    body = json.loads(router.calls[NV][0].content)
    assert body["response_format"] == {"type": "json_object"} and "JSON schema" in body["messages"][0]["content"]


async def test_fenced_json_and_think_block(router):
    router.add(NV, ok('<think>hmm {"no":1}</think>\n```json\n{"ok": true, "n": 2}\n```'))
    out, _ = await call()
    assert out.n == 2


async def test_repair_success(router):
    router.add(NV, ok('{"ok": "maybe"}'), ok('{"ok": true, "n": 3}'))
    out, m = await call()
    assert out.n == 3 and m.llm_calls == 2
    assert "invalid" in json.loads(router.calls[NV][1].content)["messages"][-1]["content"]


async def test_response_format_rejected_retries_without(router):
    router.add(NV, (400, {"error": {"message": "response_format not supported"}}), ok('{"ok": true, "n": 4}'))
    out, _ = await call()
    assert out.n == 4 and "response_format" not in json.loads(router.calls[NV][1].content)


async def test_429_falls_back(router):
    router.add(NV, (429, {"error": {"message": "rate"}}))
    router.add(GQ, ok('{"ok": true, "n": 5}'))
    out, m = await call()
    assert out.n == 5 and m.provider == "groq" and m.fallback_used


async def test_invalid_twice_falls_back(router):
    router.add(NV, ok("not json"))
    router.add(GQ, ok('{"ok": false, "n": 6}'))
    out, _ = await call()
    assert out.n == 6


async def test_all_fail_raises(router):
    router.add(NV, (500, {"error": {"message": "boom"}}))
    router.add(GQ, httpx2.ConnectError("down"))
    with pytest.raises(AppError) as e:
        await call()
    assert e.value.type == "llm_unavailable"


async def test_cache_hit_makes_no_http_calls(router):
    router.add(NV, ok('{"ok": true, "n": 7}'))
    await call()
    out, m = await call()
    assert out.n == 7 and m.cached and len(router.calls[NV]) == 1


def test_clean_json_text():
    assert client.clean_json_text('Sure! {"a": {"b": 1}} done') == '{"a": {"b": 1}}'
