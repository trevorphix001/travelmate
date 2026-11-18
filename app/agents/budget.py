"""Budget check: deterministic Python, integer rupees. Over budget -> next downgrade, at most twice.

Downgrade order (PRD section 6, rule 4): stay tier, then transport mode, then paid activities.
"""
import logging

from app.agents.costs_data import load_cost_rows
from app.agents.itinerary import choose_transport
from app.core.config import get_settings
from app.schemas.data import CostRow
from app.schemas.place import Confidence, Place
from app.schemas.plan import BudgetBreakdown, BudgetLine, DayPlan, TransportOption
from app.schemas.trip import TripRequest
from app.tools import costs

log = logging.getLogger(__name__)
MAX_RETRIES = 2
STAY_STYLES = ["mid", "budget"]  # most comfortable first
APPROX_NOTE = "Prices are approximate and may differ from current rates"


def over_budget_note(gap: int) -> str:
    return f"Even with the cheapest options, this trip is Rs {gap:,} over your budget"


INITIAL = {"stay_style": "mid", "transport_mode": None, "include_paid": True}


def is_paid(p: Place, rows: list[CostRow]) -> bool:
    """Paid unless OSM says fee=no or the cost table's entry fee for its kind is zero."""
    return p.fee is not False and costs.entry_fee(rows, p.kind, 1, p.name, p.id).total > 0


def build_breakdown(req: TripRequest, days: list[DayPlan], places: dict[str, Place], options: list[TransportOption],
                    rows: list[CostRow], dg: dict, version: str) -> BudgetBreakdown:
    lines: list[BudgetLine] = []
    chosen = choose_transport(options, dg.get("transport_mode"))
    if chosen:
        line = costs.intercity_cost(rows, chosen.mode, chosen.km, req.travellers)
        lines.append(line)
    nights = req.num_days - 1
    if nights > 0:
        lines.append(costs.stay_cost(rows, dg["stay_style"], nights, costs.rooms_for(req.travellers)))
    lines.append(costs.meal_cost(rows, req.travellers, 3 * req.num_days))
    lines.append(costs.local_transport_cost(rows, req.num_days))
    for day in days:
        for stop in day.stops:
            p = places[stop.place_id]
            fee = costs.entry_fee(rows, p.kind, req.travellers, p.name, p.id)
            if is_paid(p, rows) or fee.confidence == Confidence.unavailable:
                lines.append(fee)
    total = sum(line.total for line in lines)
    gap = max(0, total - req.budget_inr)
    return BudgetBreakdown(
        lines=lines, grand_total=total, per_person=total // req.travellers, budget_inr=req.budget_inr,
        within_budget=gap == 0, gap_inr=gap, stay_style=dg["stay_style"],
        transport_mode=chosen.mode if chosen else None, include_paid_activities=dg["include_paid"],
        suggestions=suggestions(lines, gap), cost_table_version=version,
    )


def suggestions(lines: list[BudgetLine], gap: int) -> list[str]:
    if gap == 0:
        return []
    out = [f"Over budget by Rs {gap}."]
    biggest = max(lines, key=lambda line: line.total, default=None)
    if biggest and biggest.total > 0:
        out.append(f"The largest cost is {biggest.category.replace('_', ' ')} (Rs {biggest.total}); "
                   f"shortening the trip or adding budget there closes the gap fastest.")
    return out


def stops_to_cut(days: list[DayPlan], places: dict[str, Place], rows: list[CostRow], travellers: int,
                 gap: int) -> list[str]:
    """Paid stops to drop, most expensive first, only until the gap is closed (all of them if it can't be)."""
    fees = sorted(((costs.entry_fee(rows, places[s.place_id].kind, travellers, "", s.place_id).total, s.place_id)
                   for d in days for s in d.stops if is_paid(places[s.place_id], rows)), reverse=True)
    cut, saved = [], 0
    for fee, pid in fees:
        if saved >= gap:
            break
        cut.append(pid)
        saved += fee
    return cut


def next_downgrade(dg: dict, options: list[TransportOption]) -> tuple[dict, str] | None:
    """Return (new downgrade state, what changed) following the fixed order, or None if nothing left."""
    i = STAY_STYLES.index(dg["stay_style"])
    if i + 1 < len(STAY_STYLES):
        return {**dg, "stay_style": STAY_STYLES[i + 1]}, "stay"
    cheapest = choose_transport(options)
    current = choose_transport(options, dg.get("transport_mode"))
    if cheapest and current and cheapest.mode != current.mode:
        return {**dg, "transport_mode": cheapest.mode}, "transport"
    if dg["include_paid"]:
        return {**dg, "include_paid": False}, "paid_activities"
    return None


async def budget_node(state: dict) -> dict:
    req: TripRequest = state["request"]
    rows, _tier = await load_cost_rows(state["dest_geo"])
    version = get_settings().cost_table_version
    dg = state.get("downgrade") or dict(INITIAL)
    places = {p.id: p for p in state.get("places") or state.get("candidates") or []}
    options = state.get("intercity_options") or []
    bd = build_breakdown(req, state.get("days") or [], places, options, rows, dg, version)
    retries = state.get("retries", 0)
    out: dict = {"budget": bd, "within_budget": bd.within_budget, "downgrade": dg, "budget_loop": False}

    if not bd.within_budget and retries < MAX_RETRIES and (step := next_downgrade(dg, options)):
        new_dg, what = step
        out.update(downgrade=new_dg, retries=retries + 1, replan=what == "paid_activities", budget_loop=True,
                   budget_cap=None)
        if what == "paid_activities":
            # Drop just enough paid stops, and don't let the re-plan pull in other paid places instead.
            days = state.get("days") or []
            scheduled = {s.place_id for d in days for s in d.stops}
            cut = stops_to_cut(days, places, rows, req.travellers, bd.gap_inr)
            unused_paid = [p.id for p in places.values() if p.id not in scheduled and is_paid(p, rows)]
            out["excluded_ids"] = cut + unused_paid
            out["budget_cap"] = f"Over budget: {len(cut)} paid stop(s) removed; keep the remaining plan compact."
        log.info("over budget by %d, downgrade: %s", bd.gap_inr, what)
        return out
    warnings = []
    if not rows:
        warnings.append("Prices for this trip aren't available yet")
    elif any(r.unverified for r in rows):
        warnings.append(APPROX_NOTE)
    if any(line.confidence == Confidence.unavailable for line in bd.lines):
        warnings.append("Some prices aren't available, so the real total may be higher")
    if not bd.within_budget:
        warnings.append(over_budget_note(bd.gap_inr))
    out["warnings"] = warnings
    out["retries"] = retries
    return out

