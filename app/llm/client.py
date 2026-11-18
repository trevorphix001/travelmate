"""complete_json(): the one way agents call an LLM.

Picks providers in order, forces JSON validated against a Pydantic schema, makes one repair call on
a validation error, falls through to the next provider on errors, caches validated outputs, and
returns token/latency metrics.
"""
import json
import logging
import os
import re
import time
from datetime import timedelta
from typing import TypeVar

import httpx2
import openai
from pydantic import BaseModel, ValidationError

from app.core import cache
from app.core.config import get_settings
from app.core.errors import AppError
from app.llm.providers import Provider, Role, build_providers, provider_order
from app.schemas.plan import NodeMetric

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
# Extraction must be quick; the planner (e.g. Nemotron Ultra 550B) needs ~10-17 s for an itinerary.
TIMEOUT_S = {"extract": 20.0, "planner": 45.0}
CACHE_TTL = timedelta(days=7)

# Tests swap in an httpx2.AsyncClient with a MockTransport (openai>=3 uses httpx2, not httpx).
HTTP_CLIENT: httpx2.AsyncClient | None = None

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def clean_json_text(text: str) -> str:
    """Strip <think> blocks and ``` fences; keep the outermost {...} object."""
    text = _THINK.sub("", text or "").strip()
    text = _FENCE.sub("", text).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start: end + 1] if start != -1 and end > start else text


def schema_prompt(schema: type[BaseModel]) -> str:
    return ("\n\nReturn only JSON matching this JSON schema, no prose:\n"
            + json.dumps(schema.model_json_schema(), separators=(",", ":")))


class _Usage:
    def __init__(self) -> None:
        self.prompt = self.completion = self.calls = 0

    def add(self, resp: object) -> None:
        self.calls += 1
        u = getattr(resp, "usage", None)
        if u:
            self.prompt += u.prompt_tokens or 0
            self.completion += u.completion_tokens or 0


async def _chat(p: Provider, model: str, messages: list[dict], max_tokens: int, temperature: float,
                usage: _Usage, timeout: float) -> str:
    client = openai.AsyncOpenAI(base_url=p.base_url, api_key=p.api_key, timeout=timeout, max_retries=0,
                                http_client=HTTP_CLIENT)
    kw = dict(model=model, messages=messages, max_tokens=max_tokens, temperature=temperature)
    if p.extra_body:
        kw["extra_body"] = p.extra_body
    try:
        resp = await client.chat.completions.create(**kw, response_format={"type": "json_object"})
    except openai.BadRequestError as e:
        if "response_format" not in str(e) and "json" not in str(e).lower():
            raise
        log.info("%s rejected response_format; retrying without it", p.name)
        resp = await client.chat.completions.create(**kw)
    usage.add(resp)
    return resp.choices[0].message.content or ""


async def _try_provider(p: Provider, role: Role, schema: type[T], messages: list[dict], max_tokens: int,
                        temperature: float, usage: _Usage) -> T | None:
    model = p.models.get(role)
    if not model:
        log.warning("%s has no model for role %s", p.name, role)
        return None
    text = await _chat(p, model, messages, max_tokens, temperature, usage, TIMEOUT_S[role])
    try:
        return schema.model_validate_json(clean_json_text(text))
    except ValidationError as e:
        log.info("%s output failed validation; one repair attempt", p.name)
        repair = messages + [
            {"role": "assistant", "content": text[:4000]},
            {"role": "user", "content": f"That JSON is invalid: {str(e)[:1500]}\nReturn the corrected JSON only."},
        ]
        text = await _chat(p, model, repair, max_tokens, temperature, usage, TIMEOUT_S[role])
        try:
            return schema.model_validate_json(clean_json_text(text))
        except ValidationError:
            log.warning("%s repair failed", p.name)
            return None


async def complete_json(role: Role, schema: type[T], system: str, user: str, max_tokens: int,
                        temperature: float = 0.2, node: str = "llm") -> tuple[T, NodeMetric]:
    t0 = time.perf_counter()
    key = {"role": role, "schema": schema.__name__, "system": system, "user": user}
    hit = await cache.lookup("llm", key)
    if hit is not None:
        try:
            return schema.model_validate(hit), NodeMetric(node=node, cached=True, ms=_ms(t0))
        except ValidationError:
            log.info("stale LLM cache entry ignored")

    providers = build_providers()
    order = [n for n in provider_order() if n in providers]
    messages = [{"role": "system", "content": system + schema_prompt(schema)}, {"role": "user", "content": user}]
    usage = _Usage()
    for i, name in enumerate(order):
        p = providers[name]
        try:
            out = await _try_provider(p, role, schema, messages, max_tokens, temperature, usage)
        except (openai.APIStatusError, openai.APIConnectionError) as e:  # 4xx/5xx, timeouts, network
            log.warning("provider %s failed (%s): %s", name, type(e).__name__, str(e)[:200])
            continue
        if out is None:
            continue
        await cache.store("llm", key, out.model_dump(mode="json"), CACHE_TTL)
        return out, NodeMetric(node=node, provider=name, model=p.models[role], prompt_tokens=usage.prompt,
                               completion_tokens=usage.completion, llm_calls=usage.calls, ms=_ms(t0),
                               fallback_used=i > 0)
    raise AppError("llm_unavailable", "No LLM provider succeeded",
                   f"tried: {', '.join(order) or 'none configured'}", 503)


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


if get_settings().langsmith_tracing:  # optional tracing, off by default
    from langsmith import traceable

    if get_settings().langsmith_api_key:
        os.environ.setdefault("LANGSMITH_API_KEY", get_settings().langsmith_api_key.get_secret_value())
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    complete_json = traceable(run_type="llm", name="complete_json")(complete_json)
