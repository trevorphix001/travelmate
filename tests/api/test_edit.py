"""Edit ops on a saved plan (one LLM call each, applied in Python)."""
import pytest

from ..fakes import DEMO_QUERY, demo_draft
from .conftest import USER_A, bearer


@pytest.fixture
async def saved(client, llm):
    # A roomy budget keeps paid places allowed, so swaps have candidates to use.
    llm.supervisor = lambda u: {"request": demo_draft(budget_inr=20000), "missing": []}
    r = await client.post("/api/v1/plans", json={"query": DEMO_QUERY}, headers=bearer(USER_A))
    return r.json()


async def edit(client, pid, text="change the plan"):
    r = await client.post(f"/api/v1/plans/{pid}/edit", json={"instruction": text}, headers=bearer(USER_A))
    assert r.status_code == 200, r.text
    return r.json()


def first_stop(plan):
    day = next(d for d in plan["days"] if d["stops"])
    return day["date"], day["stops"][0]["place_id"]


async def test_remove_stop(client, llm, saved):
    d, pid = first_stop(saved)
    llm.edit = lambda p: {"op": "remove_stop", "args": {"date": d, "place_id": pid}}
    out = await edit(client, saved["id"], "remove that stop")
    assert out["diff"]["removed"] == [pid] and d in out["diff"]["changed_dates"]
    assert out["plan"]["budget"]["grand_total"] == sum(x["total"] for x in out["plan"]["budget"]["lines"])
    assert len(llm.calls) and llm.calls[-1] == "EditOp"


async def test_replace_stop_uses_unused_candidate(client, llm, saved):
    # Day 2 is a full sightseeing day; the arrival day's short driving budget leaves no room to swap.
    day = saved["days"][1]
    d, pid = day["date"], day["stops"][0]["place_id"]
    llm.edit = lambda p: {"op": "replace_stop", "args": {"date": d, "place_id": pid}}
    out = await edit(client, saved["id"])
    known = {p["id"] for p in saved["places"]}
    assert pid in out["diff"]["removed"] and out["diff"]["added"] and set(out["diff"]["added"]) <= known


async def test_add_stop_by_interest(client, llm, saved):
    d = saved["days"][1]["date"]
    llm.edit = lambda p: {"op": "add_stop", "args": {"date": d, "interest": "nature"}}
    out = await edit(client, saved["id"])
    if out.get("needs_clarification"):
        assert "nature" in out["question"]
    else:
        assert out["diff"]["added"]


async def test_cheaper_stay_and_transport(client, llm, saved):
    llm.edit = lambda p: {"op": "change_transport", "args": {"mode": "private_cab"}}
    cab = await edit(client, saved["id"])
    assert cab["plan"]["budget"]["transport_mode"] == "private_cab"
    llm.edit = lambda p: {"op": "change_transport", "args": {"mode": "hovercraft"}}
    assert (await edit(client, saved["id"]))["needs_clarification"]


async def test_change_pace_and_regenerate(client, llm, saved):
    llm.edit = lambda p: {"op": "change_pace", "args": {"pace": "relaxed"}}
    out = await edit(client, saved["id"])
    assert out["plan"]["request"]["pace"] == "relaxed"
    assert all(len(d["stops"]) <= 3 for d in out["plan"]["days"])
    d = out["plan"]["days"][0]["date"]
    llm.edit = lambda p: {"op": "regenerate_day", "args": {"date": d}}
    out2 = await edit(client, saved["id"])
    assert out2.get("plan") or out2.get("needs_clarification")


async def test_nonexistent_place_clarifies(client, llm, saved, plan_repo):
    d = saved["days"][0]["date"]
    llm.edit = lambda p: {"op": "remove_stop", "args": {"date": d, "place_id": "osm:node/1"}}
    out = await edit(client, saved["id"])
    assert out["needs_clarification"] and plan_repo.edits == []
    llm.edit = lambda p: {"op": "clarify", "question": "Which stop?"}
    assert (await edit(client, saved["id"]))["question"]


async def test_replace_on_arrival_day_explains_limit(client, llm, saved):
    d, pid = saved["days"][0]["date"], saved["days"][0]["stops"][0]["place_id"]
    llm.edit = lambda p: {"op": "replace_stop", "args": {"date": d, "place_id": pid}}
    out = await edit(client, saved["id"])
    assert out.get("plan") or "driving limits" in out["question"]


async def test_stateless_edit_round_trip(client, llm, saved):
    """Browser storage mode: the browser sends the whole plan; nothing is read from or written to a DB."""
    d, pid = first_stop(saved)
    llm.edit = lambda p: {"op": "remove_stop", "args": {"date": d, "place_id": pid}}
    body = {k: v for k, v in saved.items() if k not in ("id", "created_at")}
    r = await client.post("/api/v1/plans/edit", json={"plan": body, "instruction": "remove that stop"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["diff"]["removed"] == [pid] and out["plan"]["id"] is None


async def test_stateless_edit_rejects_tampered_plan(client, saved):
    body = {k: v for k, v in saved.items() if k not in ("id", "created_at")}
    body["budget"]["grand_total"] += 1  # totals must equal the sum of the lines
    r = await client.post("/api/v1/plans/edit", json={"plan": body, "instruction": "remove that stop"})
    assert r.status_code == 422


async def test_health_reports_browser_storage_without_db(client):
    h = (await client.get("/api/v1/health")).json()
    assert h["storage"] == ("server" if h["db"] else "browser") and h["planner"] in ("llm", "robust")


async def test_robust_planner_end_to_end(client, llm, monkeypatch):
    """PLANNER=robust: no itinerary LLM call; days carry a rain chance; every stop is a known place."""
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "planner", "robust")
    llm.supervisor = lambda u: {"request": demo_draft(budget_inr=20000), "missing": []}
    r = await client.post("/api/v1/plans", json={"query": DEMO_QUERY})
    assert r.status_code == 201, r.text
    plan = r.json()
    known = {p["id"] for p in plan["places"]}
    assert all(d["rain_chance"] is not None for d in plan["days"])
    assert {s["place_id"] for d in plan["days"] for s in d["stops"]} <= known
    assert {i for d in plan["days"] for i in d["plan_b"]} <= known
    assert "ItinOut" not in llm.calls


async def test_reference_data_from_csv_without_db():
    from app.db import repo
    rows = await repo.get_cost_rows(2, "v1")
    assert rows and all(r.city_tier in (0, 2) for r in rows)
    assert (await repo.get_city("shillong")).state == "Meghalaya"
    assert [c.name for c in await repo.search_cities("Shil")] == ["Shillong"]
