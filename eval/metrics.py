"""Evaluation metrics, all computed in code from a TripPlan (PRD section 8)."""
from app.schemas.plan import TripPlan

# Latency is wall-clock time measured by eval/run.py (node times overlap in the parallel branch).


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def budget_ok(plan: TripPlan, expect_infeasible: bool) -> bool:
    """1 if total <= budget, or (for impossible cases) the plan correctly says within_budget=false."""
    if expect_infeasible:
        return plan.budget.within_budget is False
    return plan.budget.grand_total <= plan.request.budget_inr


def feasible(plan: TripPlan) -> bool:
    """1 if no day runs past its window, every stop has an arrival leg, and no place repeats."""
    ids = [s.place_id for d in plan.days for s in d.stops]
    if len(ids) != len(set(ids)):
        return False
    for d in plan.days:
        if d.end_time and _minutes(d.end_time) > _minutes(d.window_end):
            return False
        if any(s.leg_from_prev is None for s in d.stops):
            return False
    return True


def preference_coverage(plan: TripPlan) -> float:
    """Share of requested interests with at least one stop tagged with that interest (1.0 if none requested)."""
    wanted = {i.value for i in plan.request.interests}
    if not wanted:
        return 1.0
    places = {p.id: p for p in plan.places}
    covered = {i for d in plan.days for s in d.stops if s.place_id in places for i in places[s.place_id].interests}
    return len(wanted & covered) / len(wanted)


def grounding_rate(plan: TripPlan, unknown_ids: int = 0) -> float:
    """Share of stops, legs and cost lines with a valid source; stops must also be known candidate IDs.
    unknown_ids counts IDs a system invented that never reached the plan (baseline only)."""
    places = {p.id: p for p in plan.places}
    ok = total = 0
    for d in plan.days:
        for s in d.stops:
            total += 2
            ok += int(s.place_id in places and bool(places[s.place_id].source.url))
            ok += int(s.leg_from_prev is not None and bool(s.leg_from_prev.source.name))
    for line in plan.budget.lines:
        total += 1
        ok += int(bool(line.source.name))
    total += unknown_ids
    return ok / total if total else 0.0


def total_tokens(plan: TripPlan) -> int:
    return sum(m.prompt_tokens + m.completion_tokens for m in plan.trace)


def llm_calls(plan: TripPlan) -> int:
    return sum(m.llm_calls for m in plan.trace)


def node_success_rate(plan: TripPlan) -> float:
    return sum(m.ok for m in plan.trace) / len(plan.trace) if plan.trace else 0.0
