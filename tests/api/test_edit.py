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
