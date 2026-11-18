import json
from datetime import timedelta

import respx

from eval import metrics, report
from eval.run import load_cases, to_request

from ..fakes import FakeLLM, install_repo, mock_http


async def demo_plan(monkeypatch):
    from app.graph.build import build_graph
    FakeLLM().install(monkeypatch)
    install_repo(monkeypatch)
    with respx.mock(assert_all_called=False) as r:
        mock_http(r)
        return (await build_graph().ainvoke({"raw_query": "demo"}))["plan"]


def test_cases_file_valid():
    cases = load_cases()
    assert len(cases) == 40 and sum(c["expect_infeasible"] for c in cases) == 5
    assert len({c["case_id"] for c in cases}) == 40
    for c in cases:
        r = to_request(c)
        assert r.num_days == c["days"]


async def test_metrics_on_demo_plan(monkeypatch):
    plan = await demo_plan(monkeypatch)
    assert metrics.feasible(plan)
    assert metrics.grounding_rate(plan) == 1.0
    assert metrics.grounding_rate(plan, unknown_ids=10) < 1.0
    assert 0 <= metrics.preference_coverage(plan) <= 1
    assert metrics.budget_ok(plan, expect_infeasible=False) == (plan.budget.grand_total <= plan.request.budget_inr)
    assert metrics.llm_calls(plan) <= 4 and metrics.total_tokens(plan) > 0
    # duplicate place -> infeasible
    dup = plan.model_copy(deep=True)
    day = next(d for d in dup.days if d.stops)
    day.stops.append(day.stops[0])
    assert not metrics.feasible(dup)
    late = plan.model_copy(deep=True)
    late.days[0].end_time = "23:59"
    assert not metrics.feasible(late)


def test_report_bootstrap_and_paired(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "RESULTS", tmp_path)
    rows = {"graph": [1, 1, 1, 0], "baseline": [1, 0, 0, 0]}
    for sysname, vals in rows.items():
        with (tmp_path / f"{sysname}.jsonl").open("w") as f:
            for i, v in enumerate(vals):
                f.write(json.dumps({"case_id": f"c{i}", "success": v, "latency_s": 1.0 + i}) + "\n")
    rep = report.build()
    g = rep["systems"]["graph"]["success"]
    assert g["mean"] == 0.75 and g["ci95"][0] <= 0.75 <= g["ci95"][1]
    assert rep["paired_graph_minus_baseline"]["success"]["mean_diff"] == 0.5
    md = report.to_markdown(rep)
    assert "| success | 0.75" in md and "bootstrap" in md
    assert report.build() == rep  # deterministic (seed 42)


def test_to_request_offsets():
    c = load_cases(1)[0]
    r = to_request(c)
    assert (r.end_date - r.start_date) == timedelta(days=c["days"] - 1)
