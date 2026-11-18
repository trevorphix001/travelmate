"""Shared fakes for agent/graph/API tests: a scripted LLM, CSV-backed repo lookups, fixture-backed HTTP."""
import csv
import json
import re
from datetime import date, timedelta
from pathlib import Path

import httpx
import respx

from app.agents import itinerary, research, supervisor
from app.agents.editor import EditOp
from app.agents.itinerary import ItinOut
from app.agents.research import RankOut
from app.agents.supervisor import SupervisorOut
from app.core.config import get_settings
from app.db import repo
from app.schemas.data import City, CostRow
from app.schemas.plan import NodeMetric
from app.tools import geocode, weather

from .conftest import load_fixture

ROOT = Path(__file__).resolve().parents[1]
COST_ROWS = [CostRow(**r) for r in csv.DictReader((ROOT / "data/cost_table.csv").open(encoding="utf-8"))]
CITIES = {r["name"].lower(): City(**r) for r in csv.DictReader((ROOT / "data/cities.csv").open(encoding="utf-8"))}
START = date.today() + timedelta(days=5)
DEMO_QUERY = "3 days from Guwahati to Shillong for 2 people, budget Rs 8000, we love waterfalls and nature"


def demo_draft(**kw) -> dict:
    d = {"origin": "Guwahati", "destination": "Shillong", "start_date": START.isoformat(), "num_days": 3,
         "travellers": 2, "budget_inr": 8000, "interests": ["waterfalls", "nature"], "pace": None}
    d.update(kw)
    return d


class FakeLLM:
    """Answers by schema; records calls. `supervisor`, `rank` and `itin` can be overridden per test."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.supervisor = lambda user: {"request": demo_draft(), "missing": [], "question": None}
        self.rank = lambda payload: {"ranked_ids": [c["id"] for c in payload["candidates"]][: payload["max_pick"]]}
        self.itin = self._round_robin
        self.edit = lambda payload: {"op": "clarify", "question": "Which one?"}
        self.fail: set[str] = set()

    @staticmethod
    def _round_robin(payload: dict) -> dict:
        days = [d["date"] for d in payload["days"]]
        out = {d: [] for d in days}
        for i, c in enumerate(payload["candidates"]):
            d = days[i % len(days)]
            if len(out[d]) < payload["max_stops"]:
                out[d].append(c["id"])
        return {"days": [{"date": d, "place_ids": ids} for d, ids in out.items()], "notes": {}}

    async def __call__(self, role, schema, system, user, max_tokens, temperature=0.2, node="llm"):
        from app.core.errors import AppError

        self.calls.append(schema.__name__)
        if schema.__name__ in self.fail:
            raise AppError("llm_unavailable", "No LLM provider succeeded", "", 503)
        payload = json.loads(user)
        handlers = {SupervisorOut: self.supervisor, RankOut: self.rank, ItinOut: self.itin, EditOp: self.edit}
        data = handlers[schema](payload)
        return schema.model_validate(data), NodeMetric(node=node, provider="fake", model="fake-1", prompt_tokens=100,
                                                       completion_tokens=20, llm_calls=1)

    def install(self, monkeypatch) -> "FakeLLM":
        for mod in (supervisor, research, itinerary):
            monkeypatch.setattr(mod, "complete_json", self)
        return self


def install_repo(monkeypatch, rows: list[CostRow] | None = None) -> None:
    async def get_cost_rows(tier, version):
        src = COST_ROWS if rows is None else rows
        return [r for r in src if r.city_tier in (0, tier)]

    async def get_city(name):
        return CITIES.get(name.strip().lower())

    monkeypatch.setattr(repo, "get_cost_rows", get_cost_rows)
    monkeypatch.setattr(repo, "get_city", get_city)


def weather_payload(start: date, days: int, codes: list[int] | None = None) -> dict:
    """Open-Meteo forecast shape (from the recorded fixture) re-dated so tests never go stale."""
    fx = load_fixture("openmeteo_forecast_shillong.json")
    d = fx["daily"]
    codes = codes or [d["weather_code"][i % len(d["weather_code"])] for i in range(days)]
    fx["daily"] = {
        "time": [(start + timedelta(days=i)).isoformat() for i in range(days)],
        "weather_code": codes,
        "temperature_2m_max": [d["temperature_2m_max"][i % 3] for i in range(days)],
        "temperature_2m_min": [d["temperature_2m_min"][i % 3] for i in range(days)],
        "precipitation_probability_max": [d["precipitation_probability_max"][i % 3] for i in range(days)],
    }
    return fx


def _osrm(request: httpx.Request) -> httpx.Response:
    """Two-point routes replay the Guwahati-Shillong fixture; anything else fails -> labelled fallback."""
    coords = re.search(r"/driving/([^?]+)", str(request.url)).group(1).split(";")
    if "/route/" in str(request.url) and len(coords) == 2 and coords[0] != coords[1]:
        return httpx.Response(200, json=load_fixture("osrm_route_guwahati_shillong.json"))
    return httpx.Response(503)


def mock_http(router: respx.MockRouter, weather_codes: list[int] | None = None, days: int = 3) -> None:
    router.get(geocode.NOMINATIM).respond(json=[])
    for url in get_settings().overpass_urls:
        router.post(url).respond(json=load_fixture("overpass_shillong_waterfalls_nature.json"))
    router.get(url__regex=r".*/(route|table)/v1/driving/.*").mock(side_effect=_osrm)
    router.get(weather.FORECAST_URL).respond(json=weather_payload(START, days, weather_codes))
    router.get(url__regex=r".*wikipedia\.org/api/rest_v1/.*").respond(json=load_fixture("wiki_summary.json"))
    router.get(url__regex=r".*wikidata\.org/.*").respond(404)
