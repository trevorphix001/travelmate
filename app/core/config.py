from functools import lru_cache
from typing import Annotated

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _csv(v: object) -> list[str]:
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return list(v)  # type: ignore[arg-type]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    nvidia_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    llm_primary: str = "nvidia"
    llm_fallbacks: Annotated[list[str], NoDecode] = ["groq", "gemini"]
    model_planner_nvidia: str = ""
    model_extract_nvidia: str = ""
    model_planner_groq: str = ""
    model_extract_groq: str = ""
    model_planner_gemini: str = ""
    model_extract_gemini: str = ""

    supabase_url: str = ""
    supabase_jwt_secret: SecretStr | None = None  # only for legacy HS256 projects; else JWKS is used
    database_url: SecretStr | None = None

    app_user_agent: str = "TravelMateAI/0.1"
    cors_origins: Annotated[list[str], NoDecode] = ["http://127.0.0.1:5500", "http://localhost:5500"]
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    admin_key: SecretStr | None = None
    geoapify_api_key: SecretStr | None = None  # optional Overpass fallback (app/tools/geoapify_places.py)
    tavily_api_key: SecretStr | None = None  # only for scripts/draft_prices.py (offline research aid)

    overpass_urls: Annotated[list[str], NoDecode] = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]
    osrm_url: str = "https://router.project-osrm.org"
    cost_table_version: str = "v1"
    plans_per_minute: int = 5
    plans_per_day: int = 50

    @field_validator("llm_fallbacks", "cors_origins", "overpass_urls", mode="before")
    @classmethod
    def _split(cls, v: object) -> list[str]:
        return _csv(v)


@lru_cache
def get_settings() -> Settings:
    return Settings()
