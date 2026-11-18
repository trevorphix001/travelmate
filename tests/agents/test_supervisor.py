from datetime import date, timedelta

from app.agents import supervisor
from app.agents.supervisor import supervisor_node

from ..fakes import START, FakeLLM, demo_draft


async def test_full_demo_sentence(monkeypatch):
    llm = FakeLLM().install(monkeypatch)
    out = await supervisor_node({"raw_query": "3 days Guwahati to Shillong...", "today": date.today()})
    r = out["request"]
    assert (r.origin, r.destination, r.travellers, r.budget_inr) == ("Guwahati", "Shillong", 2, 8000)
    assert r.end_date == START + timedelta(days=2)  # num_days resolved in Python
    assert [i.value for i in r.interests] == ["waterfalls", "nature"] and len(llm.calls) == 1


async def test_missing_budget_asks_question(monkeypatch):
    llm = FakeLLM()
    llm.supervisor = lambda u: {"request": demo_draft(budget_inr=None), "missing": ["budget_inr"], "question": "x"}
    llm.install(monkeypatch)
    out = await supervisor_node({"raw_query": "trip", "today": date.today()})
    assert "budget" in out["question"] and "request" not in out


async def test_past_dates_is_error_question(monkeypatch):
    llm = FakeLLM()
    llm.supervisor = lambda u: {"request": demo_draft(start_date="2020-01-01"), "missing": [], "question": None}
    llm.install(monkeypatch)
    out = await supervisor_node({"raw_query": "trip", "today": date.today()})
    assert "past" in out["question"] and out["errors"]


async def test_unknown_interests_dropped(monkeypatch):
    llm = FakeLLM()
    llm.supervisor = lambda u: {"request": demo_draft(interests=["waterfalls", "skydiving"]), "missing": []}
    llm.install(monkeypatch)
    out = await supervisor_node({"raw_query": "trip", "today": date.today()})
    assert [i.value for i in out["request"].interests] == ["waterfalls"]


async def test_structured_input_skips_llm(monkeypatch):
    llm = FakeLLM().install(monkeypatch)
    req = {"origin": "Guwahati", "destination": "Shillong", "start_date": START.isoformat(),
           "end_date": (START + timedelta(days=1)).isoformat(), "travellers": 2, "budget_inr": 6000}
    out = await supervisor_node({"request_input": req})
    assert out["request"].num_days == 2 and llm.calls == []
    bad = await supervisor_node({"request_input": {**req, "travellers": 0}})
    assert bad["errors"] and "travellers" in bad["question"] and llm.calls == []


def test_query_is_truncated():
    assert supervisor.MAX_QUERY_CHARS == 300
