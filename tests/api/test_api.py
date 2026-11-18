import json
from datetime import timedelta

from ..fakes import DEMO_QUERY, START, demo_draft
from .conftest import USER_A, USER_B, bearer, token


async def test_health(client):
    r = await client.get("/api/v1/health")
    assert r.status_code == 200 and set(r.json()["providers"]) == {"nvidia", "groq", "gemini"}


async def test_parse_ok_and_clarification(client, llm):
    r = await client.post("/api/v1/plans/parse", json={"query": DEMO_QUERY})
    assert r.status_code == 200 and r.json()["destination"] == "Shillong"
    llm.supervisor = lambda u: {"request": demo_draft(budget_inr=None), "missing": []}
    r = await client.post("/api/v1/plans/parse", json={"query": "trip to Shillong"})
    assert r.json()["needs_clarification"] is True and "budget" in r.json()["question"]


async def test_create_anonymous_not_saved(client, plan_repo):
    r = await client.post("/api/v1/plans", json={"query": DEMO_QUERY})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] is None and len(body["days"]) == 3 and plan_repo.plans == {}


async def test_create_signed_in_is_saved_and_listed(client, plan_repo):
    r = await client.post("/api/v1/plans", json={"query": DEMO_QUERY}, headers=bearer(USER_A))
    pid = r.json()["id"]
    assert pid in plan_repo.plans
    lst = (await client.get("/api/v1/plans", headers=bearer(USER_A))).json()
    assert [p["id"] for p in lst] == [pid]
    got = await client.get(f"/api/v1/plans/{pid}", headers=bearer(USER_A))
    assert got.status_code == 200 and got.json()["id"] == pid


async def test_structured_request_and_validation_error(client, llm):
    req = {"origin": "Guwahati", "destination": "Shillong", "start_date": START.isoformat(),
           "end_date": (START + timedelta(days=1)).isoformat(), "travellers": 2, "budget_inr": 9000,
           "interests": ["waterfalls"]}
    r = await client.post("/api/v1/plans", json={"request": req})
    assert r.status_code == 201 and "SupervisorOut" not in llm.calls
    r = await client.post("/api/v1/plans", json={"request": {**req, "travellers": 99}})
    assert r.status_code == 422 and r.json()["type"] == "needs_clarification"


async def test_clarification_is_422_problem_json(client, llm):
    llm.supervisor = lambda u: {"request": {"destination": "Shillong"}, "missing": []}
    r = await client.post("/api/v1/plans", json={"query": "Shillong please"})
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/problem+json")
    assert set(r.json()) == {"type", "title", "detail"}


async def test_bad_body_and_size_caps(client):
    assert (await client.post("/api/v1/plans", json={})).status_code == 422
    assert (await client.post("/api/v1/plans", json={"query": "x" * 301})).status_code == 422
    big = json.dumps({"query": "a" * 5000})
    r = await client.post("/api/v1/plans", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413


async def test_rate_limit(client, monkeypatch):
    from app.api import limits
    from app.core.config import Settings
    monkeypatch.setattr(limits, "get_settings", lambda: Settings(_env_file=None, plans_per_minute=2))
    codes = [(await client.post("/api/v1/plans", json={"request": {}})).status_code for _ in range(3)]
    assert codes[-1] == 429


async def test_auth_required_expired_and_owner(client, plan_repo):
    assert (await client.get("/api/v1/plans")).status_code == 401
    expired = {"Authorization": f"Bearer {token(USER_A, exp_in=-10)}"}
    r = await client.get("/api/v1/plans", headers=expired)
    assert r.status_code == 401 and r.json()["detail"] == "Token expired"
    bad = {"Authorization": "Bearer not.a.jwt"}
    assert (await client.get("/api/v1/plans", headers=bad)).status_code == 401
    pid = (await client.post("/api/v1/plans", json={"query": DEMO_QUERY}, headers=bearer(USER_A))).json()["id"]
    assert (await client.get(f"/api/v1/plans/{pid}", headers=bearer(USER_B))).status_code == 404
    assert (await client.delete(f"/api/v1/plans/{pid}", headers=bearer(USER_B))).status_code == 404
    assert (await client.delete(f"/api/v1/plans/{pid}", headers=bearer(USER_A))).status_code == 204
    assert (await client.get(f"/api/v1/plans/{pid}", headers=bearer(USER_A))).status_code == 404


async def test_places_search_and_weather(client, monkeypatch):
    from app.db import repo

    from ..fakes import CITIES

    async def search(prefix, limit=8):
        return [c for n, c in CITIES.items() if n.startswith(prefix.lower())][:limit]

    monkeypatch.setattr(repo, "search_cities", search)
    r = await client.get("/api/v1/places/search", params={"q": "shi"})
    assert [c["name"] for c in r.json()] == ["Shillong", "Shimla"]
    r = await client.get("/api/v1/weather", params={"lat": 25.57, "lon": 91.88, "start": START.isoformat(),
                                                     "end": (START + timedelta(days=2)).isoformat()})
    assert r.status_code == 200 and len(r.json()) == 3
    r = await client.get("/api/v1/weather", params={"lat": 25.57, "lon": 91.88, "start": START.isoformat(),
                                                     "end": (START - timedelta(days=2)).isoformat()})
    assert r.status_code == 422


def parse_sse(text: str) -> list[tuple[str, str]]:
    frames = []
    for block in text.replace(chr(13), "").split(chr(10) * 2):
        ev = next((ln[6:].strip() for ln in block.splitlines() if ln.startswith("event:")), None)
        data = chr(10).join(ln[5:].strip() for ln in block.splitlines() if ln.startswith("data:"))
        if ev:
            frames.append((ev, data))
    return frames


async def test_stream_events_in_order(client):
    async with client.stream("POST", "/api/v1/plans/stream", json={"query": DEMO_QUERY}) as r:
        text = "".join([chunk async for chunk in r.aiter_text()])
    frames = parse_sse(text)
    events = [e for e, _ in frames]
    nodes = [json.loads(d)["node"] for e, d in frames if e == "node"]
    assert events[-1] == "done" and json.loads(frames[-1][1])["days"]
    assert nodes[0] == "supervisor" and nodes[1] == "geo" and nodes[-1] == "aggregator"
    assert set(nodes[2:5]) == {"research", "weather", "transport"} and "itinerary" in nodes


async def test_stream_error_event(client, llm):
    llm.supervisor = lambda u: {"request": {"destination": "Shillong"}, "missing": []}
    async with client.stream("POST", "/api/v1/plans/stream", json={"query": "Shillong please"}) as r:
        text = "".join([chunk async for chunk in r.aiter_text()])
    assert "event: error" in text and "needs_clarification" in text


async def test_eval_endpoints_need_admin_key(client, monkeypatch):
    from app.api import plans
    from app.core.config import Settings
    assert (await client.post("/api/v1/eval/run", json={"limit": 1})).status_code == 403
    monkeypatch.setattr(plans, "get_settings", lambda: Settings(_env_file=None, admin_key="adm"))
    bad = await client.post("/api/v1/eval/run", json={"limit": 1}, headers={"X-Admin-Key": "nope"})
    assert bad.status_code == 403
    r = await client.get("/api/v1/eval/report")
    assert r.status_code == 200 and "systems" in r.json()


async def _anonymous_plan(client):
    r = await client.post("/api/v1/plans", json={"query": DEMO_QUERY})
    assert r.status_code == 201 and r.json()["id"] is None
    return r.json()


async def test_save_existing_plan_after_sign_in(client, plan_repo):
    plan = await _anonymous_plan(client)
    assert (await client.post("/api/v1/plans/save", json=plan)).status_code == 401  # must sign in
    r = await client.post("/api/v1/plans/save", json=plan, headers=bearer(USER_A))
    assert r.status_code == 201, r.text
    saved = r.json()
    assert saved["id"] in plan_repo.plans and saved["budget"]["grand_total"] == plan["budget"]["grand_total"]
    lst = (await client.get("/api/v1/plans", headers=bearer(USER_A))).json()
    assert [p["id"] for p in lst] == [saved["id"]]


async def test_save_rejects_tampered_plan(client, plan_repo):
    plan = await _anonymous_plan(client)
    forged = json.loads(json.dumps(plan))
    day = next(d for d in forged["days"] if d["stops"])
    day["stops"][0]["place_id"] = "osm:node/999999999"
    r = await client.post("/api/v1/plans/save", json=forged, headers=bearer(USER_A))
    assert r.status_code == 422 and plan_repo.plans == {}
    wrong_total = json.loads(json.dumps(plan))
    wrong_total["budget"]["grand_total"] += 1
    assert (await client.post("/api/v1/plans/save", json=wrong_total, headers=bearer(USER_A))).status_code == 422


async def test_only_save_accepts_large_bodies(client):
    from app.api.limits import MAX_BODY_BYTES
    plan = await _anonymous_plan(client)
    assert len(json.dumps(plan)) > MAX_BODY_BYTES  # a real trip is bigger than the normal cap
    big = json.dumps({"query": "a" * 5000})
    r = await client.post("/api/v1/plans", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413
