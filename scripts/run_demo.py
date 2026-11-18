"""Run the graph live for the PRD demo request and print a compact plan.

Run: python -m scripts.run_demo ["your own request"]
Saves the TripPlan JSON to tests/fixtures/golden_shillong.json (demo request only).
"""
import asyncio
import logging
import sys
import time
from datetime import date
from pathlib import Path

from app.core.logging import setup_logging
from app.db.pool import close_pool, open_pool
from app.graph.build import build_graph
from app.schemas.plan import TripPlan

DEMO = "3 days from Guwahati to Shillong for 2 people starting next Friday, budget Rs 8000, waterfalls and nature"
GOLDEN = Path("tests/fixtures/golden_shillong.json")


def render(plan: TripPlan) -> str:
    names = {p.id: p for p in plan.places}
    out = [f"\n== {plan.title} ({plan.request.start_date} to {plan.request.end_date}, "
           f"{plan.request.travellers} people, budget Rs {plan.request.budget_inr})"]
    wx = {w.date: w for w in plan.weather}
    for d in plan.days:
        w = wx.get(d.date)
        wtxt = f"{w.label} {w.tmin_c}-{w.tmax_c}C ({w.kind})" if w else "weather n/a"
        out.append(f"\n-- {d.date}  window {d.window_start}-{d.window_end}, ends {d.end_time}  | {wtxt}")
        for s in d.stops:
            p = names[s.place_id]
            leg = s.leg_from_prev
            out.append(f"  {s.arrive}-{s.depart}  {p.name} [{p.kind}]  +{leg.km}km/{leg.minutes}min "
                       f"({leg.confidence})  {p.source.url}")
        for m in d.meals:
            out.append(f"  {m.start}-{m.end}  lunch")
        out += [f"  note: {n}" for n in d.notes]
    b = plan.budget
    out.append(f"\n-- Budget (stay={b.stay_style}, transport={b.transport_mode}, paid={b.include_paid_activities})")
    for line in b.lines:
        out.append(f"  {line.category:<16} {line.unit_cost:>6} x {line.qty:<3} = {line.total:>7}  {line.description}")
    out.append(f"  TOTAL Rs {b.grand_total} (per person {b.per_person}); within_budget={b.within_budget} "
               f"gap={b.gap_inr}")
    out += [f"  suggestion: {s}" for s in b.suggestions]
    out += [f"! {w}" for w in plan.warnings]
    toks = sum(m.prompt_tokens + m.completion_tokens for m in plan.trace)
    calls = sum(m.llm_calls for m in plan.trace)
    out.append("\n-- Trace: " + ", ".join(f"{m.node} {m.ms}ms" + (f" [{m.provider}]" if m.provider else "")
                                         for m in plan.trace))
    out.append(f"   LLM calls={calls} tokens={toks}")
    return "\n".join(out)


async def main(query: str) -> None:
    await open_pool()
    t0 = time.perf_counter()
    state = await build_graph().ainvoke({"raw_query": query, "today": date.today()})
    secs = time.perf_counter() - t0
    await close_pool()
    if state.get("question"):
        sys.stdout.write(f"Needs clarification: {state['question']}\n")
        return
    if state.get("errors"):
        sys.stdout.write(f"Errors: {state['errors']}\n")
        return
    plan: TripPlan = state["plan"]
    sys.stdout.write(render(plan) + f"\n   total {secs:.1f}s\n")
    if query == DEMO:
        GOLDEN.write_text(plan.model_dump_json(indent=1), encoding="utf-8")
        sys.stdout.write(f"saved {GOLDEN}\n")


if __name__ == "__main__":
    setup_logging(logging.WARNING)
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else DEMO))
