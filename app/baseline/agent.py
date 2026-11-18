"""Single-agent ReAct baseline for evaluation (PRD section 8).

Same tools and same LLM layer as the graph, but one agent decides every step. Its final answer is
scheduled and costed by the same Python code, then validated WITHOUT repair, so quality is measured raw.
"""
import json
import logging
import time
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel

from app.agents.aggregator import collect_sources
from app.agents.budget import INITIAL, build_breakdown
from app.agents.costs_data import load_cost_rows
from app.agents.itinerary import choose_transport, day_windows, schedule_day
from app.agents.research import compact
from app.core.config import get_settings
from app.core.errors import AppError
from app.llm import prompts
from app.llm.client import complete_json
from app.schemas.place import GeoPoint, Place
from app.schemas.plan import NodeMetric, TransportOption, TripPlan
from app.schemas.trip import Interest, TripRequest
from app.tools import costs
from app.tools.attractions import find_places
from app.tools.geocode import geocode
from app.tools.routing import route
from app.tools.weather import daily_weather

log = logging.getLogger(__name__)
MAX_STEPS = 8


class Action(BaseModel):
    tool: Literal["geocode", "find_places", "route", "daily_weather", "cost_lookup", "finish"]
    args: dict = {}


class BaselineResult(BaseModel):
    plan: TripPlan | None = None
    unknown_ids: int = 0  # IDs the agent invented (not returned by find_places)
    steps: int = 0
    trace: list[NodeMetric] = []
    error: str | None = None


class _Ctx:
    def __init__(self, req: TripRequest) -> None:
        self.req = req
        self.geo: dict[str, GeoPoint] = {}
        self.places: dict[str, Place] = {}
        self.options: list[TransportOption] = []
        self.weather = []

    async def point(self, name: str) -> GeoPoint | None:
        if name not in self.geo:
            g = await geocode(name)
            if g:
                self.geo[name] = g
        return self.geo.get(name)


async def _run_tool(ctx: _Ctx, a: Action) -> dict:
    args = a.args
    if a.tool == "geocode":
        g = await ctx.point(str(args.get("query", "")))
        return {"found": bool(g), **({"lat": g.lat, "lon": g.lon} if g else {})}
    if a.tool == "find_places":
        g = await ctx.point(str(args.get("place", ctx.req.destination)))
        if not g:
            return {"error": "place not found"}
        interests = [Interest(i) for i in args.get("interests", []) if i in Interest.__members__]
        found = (await find_places(g, interests or list(ctx.req.interests)))[:40]
        ctx.places.update({p.id: p for p in found})
        return {"places": [compact(p) for p in found]}
    if a.tool == "route":
        x, y = await ctx.point(str(args.get("from_place", ""))), await ctx.point(str(args.get("to_place", "")))
        if not (x and y):
            return {"error": "place not found"}
        leg = await route(x, y)
        return {"km": leg.km, "minutes": leg.minutes}
    if a.tool == "daily_weather":
        g = await ctx.point(str(args.get("place", ctx.req.destination)))
        if not g:
            return {"error": "place not found"}
        ctx.weather = await daily_weather(g, ctx.req.start_date, ctx.req.end_date)
        return {"days": [{"date": w.date.isoformat(), "label": w.label} for w in ctx.weather]}
    if a.tool == "cost_lookup":
        o, d = await ctx.point(ctx.req.origin), await ctx.point(ctx.req.destination)
        if not (o and d):
            return {"error": "origin or destination not found"}
        rows, _ = await load_cost_rows(d)
        leg = await route(o, d)
        ctx.options = [TransportOption(mode=m, km=leg.km, minutes=round(leg.minutes * (costs.duration_factor(rows, m)
                       or 1)), total_inr=(ln := costs.intercity_cost(rows, m, leg.km, ctx.req.travellers)).total,
                       description=ln.description, leg_source=leg.source, source=ln.source, confidence="estimated")
                       for m in costs.items(rows, "intercity")]
        return {"transport_modes": [o.mode for o in ctx.options], "stay_styles": costs.items(rows, "stay")}
    return {"error": "unknown tool"}


async def _finish(ctx: _Ctx, args: dict) -> tuple[TripPlan, int]:
    req = ctx.req
    origin, dest = await ctx.point(req.origin), await ctx.point(req.destination)
    if not (origin and dest):
        raise AppError("baseline_failed", "Baseline could not resolve places", "", 500)
    chosen = choose_transport(ctx.options, args.get("transport_mode"))
    windows = {d: (s, e) for d, s, e in day_windows(req, chosen.minutes if chosen else 0)}
    rank = {pid: i for i, pid in enumerate(ctx.places)}
    unknown = 0
    days = []
    by_date = {str(d.get("date")): d.get("place_ids", []) for d in args.get("days", []) if isinstance(d, dict)}
    for i in range(req.num_days):
        d = req.start_date + timedelta(days=i)
        ids = by_date.get(d.isoformat(), [])
        unknown += sum(1 for x in ids if x not in ctx.places)
        known = [x for x in dict.fromkeys(ids) if x in ctx.places]
        days.append(await schedule_day(d, known, ctx.places, rank, dest, *windows[d], [], trim=False))
    rows, _ = await load_cost_rows(dest)
    style = args.get("stay_style") if args.get("stay_style") in costs.items(rows, "stay") else INITIAL["stay_style"]
    dg = {"stay_style": style, "transport_mode": chosen.mode if chosen else None, "include_paid": True}
    budget = build_breakdown(req, days, ctx.places, ctx.options, rows, dg, get_settings().cost_table_version)
    plan = TripPlan(title=f"{req.origin} to {req.destination} (baseline)", request=req, origin_geo=origin,
                    dest_geo=dest, days=days, places=list(ctx.places.values()), budget=budget, weather=ctx.weather,
                    intercity_options=ctx.options)
    plan.sources = collect_sources(plan)
    return plan, unknown


async def run_baseline(req: TripRequest, today: date | None = None) -> BaselineResult:
    ctx = _Ctx(req)
    history: list[dict] = [{"request": req.model_dump(mode="json"), "today": (today or date.today()).isoformat()}]
    trace: list[NodeMetric] = []
    for step in range(1, MAX_STEPS + 1):
        t0 = time.perf_counter()
        try:
            action, m = await complete_json("planner", Action, prompts.BASELINE_AGENT_SYSTEM,
                                            json.dumps(history, ensure_ascii=False)[-12000:], max_tokens=900,
                                            temperature=0, node=f"baseline_{step}")
        except AppError as e:
            return BaselineResult(steps=step, trace=trace, error=e.title)
        if action.tool == "finish":
            try:
                plan, unknown = await _finish(ctx, action.args)
            except Exception as e:  # noqa: BLE001 - a malformed finish is a measured failure
                return BaselineResult(steps=step, trace=[*trace, m], error=f"finish failed: {e}")
            m.ms = int((time.perf_counter() - t0) * 1000)
            plan.trace = [*trace, m]
            return BaselineResult(plan=plan, unknown_ids=unknown, steps=step, trace=plan.trace)
        try:
            result = await _run_tool(ctx, action)
        except Exception as e:  # noqa: BLE001 - tool errors are fed back to the agent
            result = {"error": str(e)[:200]}
        m.ms = int((time.perf_counter() - t0) * 1000)
        trace.append(m)
        history.append({"action": action.model_dump(), "result": result})
    return BaselineResult(steps=MAX_STEPS, trace=trace, error="step limit reached without finish")
