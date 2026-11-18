"""Plan edits: one small LLM call maps the instruction to a closed set of ops; Python applies them.

Only the affected days are re-scheduled and the budget recomputed; no other node re-runs.
"""
import json
import logging
from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.agents.aggregator import collect_sources, validate_plan
from app.agents.budget import build_breakdown, is_paid, over_budget_note
from app.agents.costs_data import load_cost_rows
from app.agents.itinerary import STOPS_PER_DAY, choose_transport, day_windows, drive_budget, schedule_day
from app.core.config import get_settings
from app.llm import prompts
from app.llm.client import complete_json
from app.schemas.place import GeoPoint, Place
from app.schemas.plan import DayPlan, NodeMetric, PlanDiff, TripPlan
from app.schemas.trip import Interest, Pace
from app.tools.geo import haversine_km

log = logging.getLogger(__name__)
Op = Literal["remove_stop", "replace_stop", "add_stop", "cheaper_stay", "change_transport", "change_pace",
             "regenerate_day", "clarify"]


class EditOp(BaseModel):
    op: Op
    args: dict = {}
    question: str | None = None


class EditResult(BaseModel):
    plan: TripPlan | None = None
    diff: PlanDiff | None = None
    question: str | None = None
    metric: NodeMetric | None = None


class Clarify(Exception):
    pass


def plan_summary(plan: TripPlan) -> dict:
    names = {p.id: p.name for p in plan.places}
    return {
        "days": [{"day": i + 1, "date": d.date.isoformat(),
                  "stops": [{"place_id": s.place_id, "name": names.get(s.place_id, "")} for s in d.stops]}
                 for i, d in enumerate(plan.days)],
        "pace": plan.request.pace.value, "stay_style": plan.budget.stay_style,
        "transport_modes": [o.mode for o in plan.intercity_options], "grand_total": plan.budget.grand_total,
    }


def _day(plan: TripPlan, args: dict) -> DayPlan:
    try:
        d = date.fromisoformat(str(args.get("date")))
    except ValueError as e:
        raise Clarify("Which day do you mean?") from e
    for day in plan.days:
        if day.date == d:
            return day
    raise Clarify("That date is not in this plan. Which day do you mean?")


def _stop_id(day: DayPlan, args: dict) -> str:
    pid = args.get("place_id")
    if pid not in {s.place_id for s in day.stops}:
        raise Clarify("I couldn't find that stop on that day. Which stop do you mean?")
    return pid


def _unused(plan: TripPlan, centre: Place | GeoPoint | None = None, interest: str | None = None,
            exclude_paid: set[str] | None = None) -> list[Place]:
    used = {s.place_id for d in plan.days for s in d.stops}
    pool = [p for p in plan.places if p.id not in used and p.id not in (exclude_paid or set())]
    if interest:
        pool = [p for p in pool if interest in p.interests]
    if centre:  # nearest to the place being replaced keeps the day's route short
        pool.sort(key=lambda p: haversine_km(centre.lat, centre.lon, p.lat, p.lon))
    return pool  # otherwise keep research rank order


async def _reschedule(plan: TripPlan, day_ids: dict[date, list[str]], pace: Pace | None = None) -> list[DayPlan]:
    req = plan.request.model_copy(update={"pace": pace}) if pace else plan.request
    chosen = choose_transport(plan.intercity_options, plan.budget.transport_mode)
    travel = chosen.minutes if chosen else 0
    windows = {d: (s, e) for d, s, e in day_windows(req, travel)}
    last = len(plan.days) - 1
    by_id = {p.id: p for p in plan.places}
    rank = {p.id: i for i, p in enumerate(plan.places)}
    out = []
    for i, day in enumerate(plan.days):
        if day.date not in day_ids:
            out.append(day)
            continue
        notes = [n for n in day.notes if not n.startswith("Skipped ")]
        ids = day_ids[day.date][: STOPS_PER_DAY[req.pace]]
        budget = drive_budget(req.pace, travel * ((i == 0) + (i == last)))
        out.append(await schedule_day(day.date, ids, by_id, rank, plan.dest_geo, *windows[day.date], notes,
                                      max_drive=budget))
    return out


MAX_TRIES = 5


def _day_centre(plan: TripPlan, day: DayPlan) -> Place:
    """The day's first stop (else the stay): new stops are searched near where the day already goes."""
    by_id = {p.id: p for p in plan.places}
    return by_id[day.stops[0].place_id] if day.stops else plan.dest_geo


async def _first_that_fits(plan: TripPlan, d: date, candidates: list[Place], new_ids, refusal: str) -> list[DayPlan]:
    """Try the nearest candidates until one survives scheduling (window + driving budget) on day d."""
    for c in candidates[:MAX_TRIES]:
        days = await _reschedule(plan, {d: new_ids(c.id)})
        if any(s.place_id == c.id for day in days if day.date == d for s in day.stops):
            return days
    raise Clarify(refusal)


async def apply_op(plan: TripPlan, op: EditOp) -> TripPlan:
    a = op.args
    days, req = plan.days, plan.request
    dg = {"stay_style": plan.budget.stay_style, "transport_mode": plan.budget.transport_mode,
          "include_paid": plan.budget.include_paid_activities}
    rows, _tier = await load_cost_rows(plan.dest_geo)
    paid = set() if dg["include_paid"] else {p.id for p in plan.places if is_paid(p, rows)}
    ids = {d.date: [s.place_id for s in d.stops] for d in plan.days}
    by_id = {p.id: p for p in plan.places}

    if op.op == "remove_stop":
        day = _day(plan, a)
        pid = _stop_id(day, a)
        days = await _reschedule(plan, {day.date: [i for i in ids[day.date] if i != pid]})
    elif op.op == "replace_stop":
        day = _day(plan, a)
        pid = _stop_id(day, a)
        alt = _unused(plan, centre=by_id[pid], exclude_paid=paid)
        if not alt:
            raise Clarify("There are no other mapped places left to swap in. Remove the stop instead?")
        days = await _first_that_fits(
            plan, day.date, alt, lambda c: [c if i == pid else i for i in ids[day.date]],
            "No other place fits that day's time and driving limits. Remove the stop instead?")
    elif op.op == "add_stop":
        day = _day(plan, a)
        interest = a.get("interest")
        if interest not in Interest.__members__:
            raise Clarify("What kind of place should I add (e.g. food, heritage, nature)?")
        alt = _unused(plan, centre=_day_centre(plan, day), interest=interest, exclude_paid=paid)
        if not alt:
            raise Clarify(f"No unused {interest} places are mapped near {plan.dest_geo.name}. Try another kind?")
        days = await _first_that_fits(plan, day.date, alt, lambda c: [*ids[day.date], c],
                                      f"No {interest} place fits that day's time and driving limits. "
                                      "Remove a stop first, or try another day?")
    elif op.op == "regenerate_day":
        day = _day(plan, a)
        fresh = _unused(plan, exclude_paid=paid)[: STOPS_PER_DAY[req.pace]]
        days = await _reschedule(plan, {day.date: [p.id for p in fresh]})
    elif op.op == "change_pace":
        if a.get("pace") not in Pace.__members__:
            raise Clarify("Which pace: relaxed, normal or packed?")
        req = req.model_copy(update={"pace": Pace(a["pace"])})
        days = await _reschedule(plan, ids, pace=req.pace)
    elif op.op == "cheaper_stay":
        if dg["stay_style"] == "budget":
            raise Clarify("The stay is already the cheapest style in the cost table. Cut a night instead?")
        dg["stay_style"] = "budget"
    elif op.op == "change_transport":
        if a.get("mode") not in {o.mode for o in plan.intercity_options}:
            modes = ", ".join(o.mode.replace("_", " ") for o in plan.intercity_options) or "none"
            raise Clarify(f"Which transport mode? Available: {modes}.")
        dg["transport_mode"] = a["mode"]
    else:
        raise Clarify(op.question or "Could you rephrase that edit?")

    budget = build_breakdown(req, days, by_id, plan.intercity_options, rows, dg, get_settings().cost_table_version)
    new = plan.model_copy(update={"request": req, "days": days, "budget": budget})
    new.warnings = [w for w in plan.warnings if not w.startswith(over_budget_note(0).split(" Rs ")[0])]
    if not budget.within_budget:
        new.warnings.append(over_budget_note(budget.gap_inr))
    new.warnings += [w for w in validate_plan(new) if w not in new.warnings]
    new.sources = collect_sources(new)
    return new


def make_diff(op: str, old: TripPlan, new: TripPlan) -> PlanDiff:
    old_ids = {d.date: [s.place_id for s in d.stops] for d in old.days}
    new_ids = {d.date: [s.place_id for s in d.stops] for d in new.days}
    before = {i for v in old_ids.values() for i in v}
    after = {i for v in new_ids.values() for i in v}
    changed = [d.date for d, o in zip(new.days, old.days, strict=True)
               if new_ids[d.date] != old_ids[o.date] or d.model_dump() != o.model_dump()]
    return PlanDiff(op=op, changed_dates=changed, added=sorted(after - before), removed=sorted(before - after),
                    old_total=old.budget.grand_total, new_total=new.budget.grand_total)


async def edit_plan(plan: TripPlan, instruction: str) -> EditResult:
    """Exactly one LLM call (role=extract), then deterministic application."""
    user = json.dumps({"plan": plan_summary(plan), "instruction": instruction[:300]}, ensure_ascii=False)
    op, metric = await complete_json("extract", EditOp, prompts.EDIT_ROUTER_SYSTEM, user, max_tokens=200,
                                     temperature=0, node="editor")
    try:
        new = await apply_op(plan, op)
    except Clarify as c:
        return EditResult(question=str(c), metric=metric)
    return EditResult(plan=new, diff=make_diff(op.op, plan, new), metric=metric)
