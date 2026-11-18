import json

import respx

from scripts import draft_prices as dp

from .conftest import load_fixture

FIXTURE = load_fixture("tavily_elephant_falls_fee.json")


def test_extract_amounts_and_free_phrases():
    text = "Entry fee: Rs. 20 per person, camera ₹ 1,000. Parking 50 rupees. Children: No entry fee."
    prices = [p for p, _ in dp.extract(text)]
    assert sorted(prices) == [0, 20, 50, 1000]
    assert all(ev for _, ev in dp.extract(text))
    assert dp.extract("Open 9 to 5, 150 steps down") == []  # bare numbers are not prices


def test_drafts_from_recorded_tavily_response():
    rows = dp.drafts_for(FIXTURE, 0, "entry_fee", "wiki:en/63011110", "person", "Elephant Falls entry fee")
    assert rows and all(r["note"].startswith("DRAFT from Tavily") for r in rows)
    assert all(r["source_url"].startswith("http") for r in rows)
    assert any(r["price_inr"] == 0 for r in rows)  # the recorded page says "No entry fee"


def test_intercity_drafts_keep_source_without_price():
    rows = dp.drafts_for(FIXTURE, 0, "intercity", "state_bus", "person_100km", "q", want_price=False)
    assert rows and all(r["price_inr"] == "" for r in rows)


async def test_search_goes_through_cache(monkeypatch, mem_cache):
    from app.core.config import Settings
    monkeypatch.setattr(dp, "get_settings", lambda: Settings(_env_file=None, tavily_api_key="tvly-test"))
    with respx.mock() as r:
        route = r.post(dp.API).respond(json=FIXTURE)
        a = await dp.search("x")
        b = await dp.search("x")
    assert a == b and route.call_count == 1
    assert route.calls[0].request.headers["authorization"] == "Bearer tvly-test"
    assert json.loads(route.calls[0].request.content)["max_results"] == dp.MAX_RESULTS


def test_plan_places_reads_stop_ids(tmp_path):
    plan = {"places": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
            "days": [{"stops": [{"place_id": "b"}, {"place_id": "a"}]}, {"stops": [{"place_id": "b"}]}]}
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan), encoding="utf-8")
    assert dp.plan_places(p) == [("b", "B"), ("a", "A")]


def test_implausible_candidates_dropped():
    res = {"results": [{"url": "https://x.test", "content": "Rooms from Rs 1,351. Call Rs 970284. Rs 2 off"}]}
    rows = dp.drafts_for(res, 2, "stay", "budget", "room_night", "q")
    assert [r["price_inr"] for r in rows] == [1351]
