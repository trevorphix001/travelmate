"""End-to-end graph test on recorded fixtures (all tools and the LLM mocked)."""
from datetime import date

import respx

from app.graph.build import NODES, build_graph

from .fakes import DEMO_QUERY, FakeLLM, install_repo, mock_http


async def run(monkeypatch, llm: FakeLLM | None = None, **state):
    llm = (llm or FakeLLM()).install(monkeypatch)
    install_repo(monkeypatch)
    with respx.mock(assert_all_called=False) as router:
        mock_http(router)
        out = await build_graph().ainvoke({"raw_query": DEMO_QUERY, "today": date.today(), **state})
    return out, llm


async def test_demo_request_end_to_end(monkeypatch):
    out, llm = await run(monkeypatch)
    assert not out.get("errors"), out.get("errors")
    plan = out["plan"]
    assert len(plan.days) == 3
    known = {p.id for p in plan.places}
    assert all(s.place_id in known for d in plan.days for s in d.stops)
    assert any(d.stops for d in plan.days)
    assert plan.budget.grand_total == sum(line.total for line in plan.budget.lines)
    assert {m.node for m in plan.trace} == set(NODES)
    assert len(llm.calls) <= 4
    assert sum(m.llm_calls for m in plan.trace) <= 4
    assert plan.sources and all(s.name for s in plan.sources)
    assert all(s.leg_from_prev and s.leg_from_prev.source.name for d in plan.days for s in d.stops)


async def test_tiny_budget_downgrades_then_flags(monkeypatch):
    from .fakes import demo_draft
    llm = FakeLLM()
    llm.supervisor = lambda u: {"request": demo_draft(budget_inr=500), "missing": [], "question": None}
    out, llm = await run(monkeypatch, llm=llm)
    llm_budget = out["plan"].budget
    assert llm_budget.within_budget is False and llm_budget.gap_inr > 0
    assert llm_budget.stay_style == "budget" and llm_budget.include_paid_activities is False
    assert out["retries"] == 2 and len(llm.calls) <= 4


async def test_missing_budget_returns_question(monkeypatch):
    llm = FakeLLM()
    llm.supervisor = lambda u: {"request": {"origin": "Guwahati", "destination": "Shillong"}, "missing": [],
                                "question": None}
    out, _ = await run(monkeypatch, llm=llm)
    assert out.get("question") and "plan" not in out
    assert [m.node for m in out["trace"]] == ["supervisor"]


async def test_unknown_destination_is_an_error(monkeypatch):
    llm = FakeLLM()
    from .fakes import demo_draft
    llm.supervisor = lambda u: {"request": demo_draft(destination="Qwxyzpur"), "missing": [], "question": None}
    out, _ = await run(monkeypatch, llm=llm)
    assert any("Qwxyzpur" in e for e in out["errors"]) and "plan" not in out
