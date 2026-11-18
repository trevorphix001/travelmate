"""OpenAI-compatible LLM providers, configured from .env. Providers without a key are skipped."""
from dataclasses import dataclass, field
from typing import Literal

from app.core.config import Settings, get_settings

Role = Literal["planner", "extract"]

# Nemotron models reason before answering unless told not to; our calls only pick IDs from given lists,
# so thinking is switched off to keep latency and tokens inside the PRD budget (~12K tokens/plan).
EXTRA_BODY: dict[str, dict] = {
    "nvidia": {"chat_template_kwargs": {"enable_thinking": False}},
    # Groq's gpt-oss models otherwise spend the reply budget reasoning and JSON mode rejects the cut-off output.
    "groq": {"reasoning_effort": "low"},
}

BASE_URLS: dict[str, str] = {
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    api_key: str
    models: dict[str, str]  # role -> model id
    extra_body: dict = field(default_factory=dict)


def build_providers(s: Settings | None = None) -> dict[str, Provider]:
    s = s or get_settings()
    out: dict[str, Provider] = {}
    for name, url in BASE_URLS.items():
        key = getattr(s, f"{name}_api_key")
        if key is None or not key.get_secret_value():
            continue
        models = {"planner": getattr(s, f"model_planner_{name}"), "extract": getattr(s, f"model_extract_{name}")}
        out[name] = Provider(name, url, key.get_secret_value(), models, EXTRA_BODY.get(name, {}))
    return out


def provider_order(s: Settings | None = None) -> list[str]:
    s = s or get_settings()
    return list(dict.fromkeys([s.llm_primary, *s.llm_fallbacks]))
