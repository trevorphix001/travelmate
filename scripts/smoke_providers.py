"""List live model IDs per provider and send one JSON-mode chat completion.

Run: python -m scripts.smoke_providers
"""
import asyncio
import json
import logging
import time

from openai import AsyncOpenAI

from app.core.config import get_settings
from app.llm.providers import BASE_URLS

log = logging.getLogger("smoke")
KEYWORDS = ("nemotron", "gpt-oss", "llama", "qwen", "gemini")


async def check(name: str, api_key: str, extract_model: str) -> None:
    client = AsyncOpenAI(base_url=BASE_URLS[name], api_key=api_key, timeout=30)
    try:
        models = await client.models.list()
        ids = sorted(m.id for m in models.data if any(k in m.id.lower() for k in KEYWORDS))
        log.info("[%s] %d matching models:\n  %s", name, len(ids), "\n  ".join(ids))
    except Exception as e:  # noqa: BLE001 - report and continue per provider
        log.error("[%s] models.list failed: %s", name, e)
    if not extract_model:
        log.warning("[%s] MODEL_EXTRACT_%s not set; skipping completion", name, name.upper())
        return
    t0 = time.perf_counter()
    try:
        r = await client.chat.completions.create(
            model=extract_model,
            messages=[{"role": "user", "content": 'Return exactly the JSON {"ok": true}'}],
            response_format={"type": "json_object"},
            max_tokens=200,
            temperature=0,
        )
        ms = int((time.perf_counter() - t0) * 1000)
        text = r.choices[0].message.content or ""
        log.info("[%s] %s -> %r  (%d ms, usage=%s)", name, extract_model, text.strip()[:80], ms,
                 r.usage.model_dump() if r.usage else None)
        if "ok" in text:
            json.loads(text[text.find("{"): text.rfind("}") + 1])
    except Exception as e:  # noqa: BLE001
        log.error("[%s] completion failed: %s", name, e)


async def main() -> None:
    s = get_settings()
    for name in ("nvidia", "groq", "gemini"):
        key = getattr(s, f"{name}_api_key")
        if key is None or not key.get_secret_value():
            log.warning("[%s] no API key; skipped", name)
            continue
        await check(name, key.get_secret_value(), getattr(s, f"model_extract_{name}"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(main())
