"""Run the benchmark: python -m eval.run --system graph|baseline|both --limit N [--provider nvidia] [--delay 4]

Writes one JSON line per (case, system) to eval/results/<system>.jsonl and resumes where it stopped.
"""
import argparse
import asyncio
import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path

from app.baseline.agent import run_baseline
from app.core.config import get_settings
from app.db.pool import close_pool, open_pool
from app.graph.build import build_graph
from app.schemas.trip import TripRequest
from eval import metrics

log = logging.getLogger("eval")
CASES = Path("eval/cases.jsonl")
RESULTS = Path("eval/results")
# Seconds between cases so free tiers are not exceeded (Groq gpt-oss-120b: 30 RPM; NVIDIA ~40 RPM).
DEFAULT_DELAY = {"nvidia": 4.0, "groq": 6.0, "gemini": 6.0}


def load_cases(limit: int | None = None) -> list[dict]:
    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()]
    return cases[:limit] if limit else cases


def to_request(case: dict, today: date | None = None) -> TripRequest:
    start = (today or date.today()) + timedelta(days=case["start_in_days"])
    return TripRequest(origin=case["origin"], destination=case["destination"], start_date=start,
                       end_date=start + timedelta(days=case["days"] - 1), travellers=case["travellers"],
                       budget_inr=case["budget_inr"], interests=case["interests"], pace=case["pace"])


def score(plan, case: dict, seconds: float, unknown_ids: int = 0) -> dict:
    return {
        "success": True,
        "budget_ok": metrics.budget_ok(plan, case["expect_infeasible"]),
        "feasible": metrics.feasible(plan),
        "preference_coverage": round(metrics.preference_coverage(plan), 3),
        "grounding_rate": round(metrics.grounding_rate(plan, unknown_ids), 3),
        "latency_s": round(seconds, 2),
        "total_tokens": metrics.total_tokens(plan),
        "llm_calls": metrics.llm_calls(plan),
        "node_success_rate": round(metrics.node_success_rate(plan), 3),
    }


FAILED = {"success": False, "budget_ok": False, "feasible": False, "preference_coverage": 0.0,
          "grounding_rate": 0.0, "node_success_rate": 0.0}


async def run_case(system: str, case: dict, graph) -> dict:
    req = to_request(case)
    t0 = time.perf_counter()
    if system == "graph":
        state = await graph.ainvoke({"request_input": req.model_dump(mode="json"), "today": date.today()})
        secs = time.perf_counter() - t0
        if state.get("errors") or "plan" not in state:
            return {**FAILED, "latency_s": round(secs, 2), "error": "; ".join(state.get("errors") or [])[:300]}
        return score(state["plan"], case, secs)
    res = await run_baseline(req)
    secs = time.perf_counter() - t0
    if res.plan is None:
        return {**FAILED, "latency_s": round(secs, 2), "llm_calls": sum(m.llm_calls for m in res.trace),
                "total_tokens": sum(m.prompt_tokens + m.completion_tokens for m in res.trace), "error": res.error}
    return score(res.plan, case, secs, res.unknown_ids)


def done_ids(system: str) -> set[str]:
    f = RESULTS / f"{system}.jsonl"
    if not f.exists():
        return set()
    return {json.loads(line)["case_id"] for line in f.read_text(encoding="utf-8").splitlines() if line.strip()}


async def main(systems: list[str], limit: int | None, delay: float) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    await open_pool()
    graph = build_graph()
    try:
        for system in systems:
            skip = done_ids(system)
            for case in load_cases(limit):
                if case["case_id"] in skip:
                    continue
                try:
                    m = await run_case(system, case, graph)
                except Exception as e:  # noqa: BLE001 - one bad case must not stop the benchmark
                    log.exception("case %s crashed", case["case_id"])
                    m = {**FAILED, "error": f"{type(e).__name__}: {e}"[:300]}
                row = {"case_id": case["case_id"], "system": system, **m}
                with (RESULTS / f"{system}.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
                log.info("%s %s success=%s budget_ok=%s feasible=%s %.1fs", system, case["case_id"],
                         m["success"], m["budget_ok"], m["feasible"], m.get("latency_s", 0))
                await asyncio.sleep(delay)
    finally:
        await close_pool()


def cli() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", choices=["graph", "baseline", "both"], default="both")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--provider", choices=["nvidia", "groq", "gemini"], default=None,
                    help="force one provider for both systems (same model for a fair comparison)")
    ap.add_argument("--delay", type=float, default=None)
    a = ap.parse_args()
    s = get_settings()
    if a.provider:
        s.llm_primary, s.llm_fallbacks = a.provider, []
    delay = a.delay if a.delay is not None else DEFAULT_DELAY.get(s.llm_primary, 5.0)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("httpx", "httpcore"):  # request URLs can carry API keys (Geoapify): never print them
        logging.getLogger(noisy).setLevel(logging.WARNING)
    systems = ["graph", "baseline"] if a.system == "both" else [a.system]
    asyncio.run(main(systems, a.limit, delay))


if __name__ == "__main__":
    cli()
