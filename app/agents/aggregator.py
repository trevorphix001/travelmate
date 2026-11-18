"""Aggregator: assemble the TripPlan and run the final grounding validators (no LLM)."""
import logging
from datetime import timedelta

from app.core.errors import AppError
from app.schemas.place import Place, Source
from app.schemas.plan import BudgetBreakdown, DayPlan, TripPlan

log = logging.getLogger(__name__)


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def drop_unknown_stops(days: list[DayPlan], known: set[str]) -> tuple[list[DayPlan], list[str]]:
    warnings, out = [], []
    for d in days:
        good = [s for s in d.stops if s.place_id in known]
        if len(good) != len(d.stops):
            log.warning("removed %d stop(s) with unknown IDs on %s", len(d.stops) - len(good), d.date)
        out.append(d.model_copy(update={"stops": good}))
    return out, warnings


def validate_plan(plan: TripPlan) -> list[str]:
    """Raise AppError for unfixable violations; return warnings for the rest."""
    warnings: list[str] = []
    known = {p.id for p in plan.places}
    if any(s.place_id not in known for d in plan.days for s in d.stops):
        raise AppError("plan_invalid", "Plan references unknown places", "", 500)
    b: BudgetBreakdown = plan.budget
    if sum(line.total for line in b.lines) != b.grand_total:
        raise AppError("plan_invalid", "Budget total does not equal the sum of its lines", "", 500)
    if any(line.total != line.unit_cost * line.qty for line in b.lines):
        raise AppError("plan_invalid", "A budget line total is not unit cost x quantity", "", 500)
    req = plan.request
    expected = [req.start_date + timedelta(days=i) for i in range(req.num_days)]
    if [d.date for d in plan.days] != expected:
        raise AppError("plan_invalid", "Plan dates do not match the request", "", 500)
    for d in plan.days:
        if d.end_time and _minutes(d.end_time) > _minutes(d.window_end):
            warnings.append(f"{d.date:%a %d %b} may run past {d.window_end}")
        for s in d.stops:
            if s.leg_from_prev is None or not s.leg_from_prev.source.name:
                raise AppError("plan_invalid", "A travel leg has no source", "", 500)
    for p in plan.places:
        if not p.source.name or not p.source.url:
            raise AppError("plan_invalid", f"Place {p.id} has no source", "", 500)
    return warnings


def _provider(source: Source) -> str:
    return "Wikipedia" if source.name.startswith("Wikipedia") else "OpenStreetMap"


def collect_sources(plan: TripPlan) -> list[Source]:
    """One readable, linked entry per thing the plan relies on (uncited items have no link and are left out)."""
    used = {s.place_id for d in plan.days for s in d.stops}
    found = [Source(name=f"{g.name} location – OpenStreetMap", url=g.source.url)
             for g in (plan.origin_geo, plan.dest_geo)]
    for p in plan.places:
        if p.id in used:
            found.append(Source(name=f"{p.name} – {_provider(p.source)}", url=p.source.url))
            if p.wiki_url and p.summary and p.wiki_url != p.source.url:
                found.append(Source(name=f"{p.name} – Wikipedia", url=p.wiki_url))
    legs = [s.leg_from_prev for d in plan.days for s in d.stops if s.leg_from_prev]
    legs += [d.return_leg for d in plan.days if d.return_leg]
    road = next((leg.source.url for leg in legs if leg.source.url), None) or next(
        (o.leg_source.url for o in plan.intercity_options if o.leg_source.url), None)
    if road:
        found.append(Source(name="Road routes and distances – OSRM (OpenStreetMap)", url=road))
    if weather := next((w.source.url for w in plan.weather if w.source.url), None):
        found.append(Source(name="Weather – Open-Meteo.com", url=weather))
    found += [Source(name=f"{line.description.split(' (')[0]} – price source", url=line.source.url)
              for line in plan.budget.lines if line.source.url]
    seen, out = set(), []
    for s in found:
        if s.url and s.url not in seen:
            seen.add(s.url)
            out.append(s)
    return out


def assemble(state: dict) -> TripPlan:
    req = state["request"]
    places: list[Place] = state.get("places") or state.get("candidates") or []
    days, warnings = drop_unknown_stops(state.get("days") or [], {p.id for p in places})
    plan = TripPlan(
        title=f"{req.origin} to {req.destination}, {req.num_days} day{'s' if req.num_days > 1 else ''}",
        request=req, origin_geo=state["origin_geo"], dest_geo=state["dest_geo"], days=days, places=places,
        budget=state["budget"], weather=state.get("weather") or [],
        intercity_options=state.get("intercity_options") or [],
        warnings=list(dict.fromkeys([*(state.get("warnings") or []), *warnings])),
        trace=state.get("trace") or [],
    )
    plan.warnings += [w for w in validate_plan(plan) if w not in plan.warnings]
    plan.sources = collect_sources(plan)
    return plan


async def aggregator_node(state: dict) -> dict:
    return {"plan": assemble(state)}
