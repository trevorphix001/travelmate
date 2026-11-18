import json
from datetime import UTC, datetime

import respx

from app.agents import research
from app.schemas.place import GeoPoint, Source
from app.schemas.trip import TripRequest
from app.tools.attractions import dedupe, to_place

from ..conftest import load_fixture
from ..fakes import START, FakeLLM, mock_http

ELEMENTS = load_fixture("overpass_shillong_waterfalls_nature.json")["elements"]
PLACES = dedupe([p for e in ELEMENTS if (p := to_place(e, datetime.now(UTC)))])
REQ = TripRequest(origin="Guwahati", destination="Shillong", start_date=START, end_date=START, travellers=2,
                  budget_inr=5000, interests=["waterfalls", "nature"], pace="relaxed")
DEST = GeoPoint(name="Shillong", lat=25.57599, lon=91.88279, tier=2, source=Source(name="t"), confidence="verified")


def state():
    return {"request": REQ, "dest_geo": DEST}


async def test_unknown_id_dropped(monkeypatch):
    llm = FakeLLM()
    llm.rank = lambda payload: {"ranked_ids": ["osm:node/999999", payload["candidates"][3]["id"]]}
    llm.install(monkeypatch)
    with respx.mock(assert_all_called=False) as r:
        mock_http(r)
        out = await research.research_node(state())
    ids = [p.id for p in out["candidates"]]
    assert "osm:node/999999" not in ids
    assert ids[0] == out["places"][3].id  # LLM order kept for known IDs
    assert len(ids) == research.max_pick(REQ)  # topped up deterministically


async def test_llm_failure_uses_deterministic_fallback(monkeypatch):
    llm = FakeLLM()
    llm.fail = {"RankOut"}
    llm.install(monkeypatch)
    with respx.mock(assert_all_called=False) as r:
        mock_http(r)
        out = await research.research_node(state())
    assert len(out["candidates"]) == research.max_pick(REQ)
    assert out["warnings"] == []  # the simpler ranking is used silently


def test_pre_rank_prefers_interest_matches_and_drops_city_node():
    pool = research.pre_rank(PLACES, REQ, "Shillong")
    assert all(p.name != "Shillong" for p in pool)
    assert "waterfalls" in pool[0].interests or "nature" in pool[0].interests


def test_compact_payload_token_budget():
    pool = research.pre_rank(PLACES * 3, REQ, "Shillong")  # worst case ~ POOL_SIZE items
    payload = json.dumps([research.compact(p) for p in pool])
    assert len(payload) / 4 < 3000


def test_distance_lowers_score_and_reaches_payload():
    near, far = PLACES[0], PLACES[0].model_copy(update={"id": "osm:node/far", "lat": PLACES[0].lat + 0.5})
    s_far = research.score(far, REQ, research.km_from(far, DEST))
    assert s_far < research.score(near, REQ, research.km_from(near, DEST))
    assert research.compact(far, 55.4)["km"] == 55
