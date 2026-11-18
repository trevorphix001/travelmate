"""Itinerary: Python clusters, the LLM assigns IDs to days, Python validates and schedules.

Times, legs and durations are all computed here; the LLM output is only a list of IDs per date.
"""
import json
import logging
import re
from datetime import date, timedelta

from pydantic import BaseModel

from app.agents.durations import visit_minutes
from app.core.errors import AppError
from app.llm import prompts
from app.llm.client import complete_json
from app.schemas.place import GeoPoint, Place
from app.schemas.plan import DayPlan, DayWeather, Meal, NodeMetric, Stop, TransportOption
from app.schemas.trip import Pace, TripRequest
from app.tools.geo import haversine_km
from app.tools.routing import LegLite, matrix, route_multi
from app.tools.weather import is_rainy

log = logging.getLogger(__name__)
WINDOWS = {Pace.relaxed: (10 * 60, 18 * 60), Pace.normal: (9 * 60, 20 * 60), Pace.packed: (8 * 60, 21 * 60)}
STOPS_PER_DAY = {Pace.relaxed: 3, Pace.normal: 4, Pace.packed: 6}
LUNCH_FROM, LUNCH_MIN = 12 * 60 + 30, 60
MAX_INTERCITY_MIN = 8 * 60
# Total road time per day a traveller will accept, including the intercity trip on arrival and departure
# days. Beyond this a day is a road trip, not sightseeing.
MAX_DRIVE_MIN = {Pace.relaxed: 3 * 60, Pace.normal: 4 * 60, Pace.packed: 5 * 60}
MIN_LOCAL_DRIVE = 60  # arrival/departure days still allow a short loop near the stay
OUTDOOR_KINDS = {"waterfall", "peak", "viewpoint", "park", "nature_reserve", "cave_entrance"}
_HHMM = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")


class DayIds(BaseModel):
    date: date
    place_ids: list[str]


class ItinOut(BaseModel):
    days: list[DayIds]
    notes: dict[str, str] = {}


# ---- small helpers ---------------------------------------------------------
def hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def is_outdoor(p: Place) -> bool:
    return p.kind in OUTDOOR_KINDS or bool({"waterfalls", "nature", "adventure"} & set(p.interests))


def opening_window(oh: str | None) -> tuple[int, int] | None:
    """Parse simple OSM opening_hours ('09:00-17:00', 'Mo-Su 10:00-18:00', '24/7'). Weekdays are ignored."""
    if not oh:
        return None
    if oh.strip() == "24/7":
        return (0, 24 * 60)
    ranges = _HHMM.findall(oh)
    if not ranges:
        return None
    opens = [int(a) * 60 + int(b) for a, b, _, _ in ranges]
    closes = [int(c) * 60 + int(d) for _, _, c, d in ranges]
    return (min(opens), max(closes))


def choose_transport(options: list[TransportOption], mode: str | None = None) -> TransportOption | None:
    """The named mode if available, else the cheapest option taking at most 8 hours."""
    if mode:
        for o in options:
            if o.mode == mode:
                return o
    ok = [o for o in options if o.minutes <= MAX_INTERCITY_MIN] or options
    return min(ok, key=lambda o: (o.total_inr, o.minutes)) if ok else None


def day_windows(req: TripRequest, travel_min: int) -> list[tuple[date, int, int]]:
    """Daily windows; the first day starts after arrival and the last ends before the trip home."""
    start, end = WINDOWS[req.pace]
    out = []
    for i in range(req.num_days):
        s, e = start, end
        if i == 0:
            s += travel_min
        if i == req.num_days - 1:
            e -= travel_min
        out.append((req.start_date + timedelta(days=i), s, max(s, e)))
    return out


# ---- clustering ------------------------------------------------------------
def cluster(cands: list[Place], m: list[list[LegLite]], n_groups: int, cap: int) -> dict[str, int]:
    """Greedy nearest-neighbour grouping on travel minutes. m is indexed [0]=centre, [i+1]=cands[i]."""
    idx = {p.id: i + 1 for i, p in enumerate(cands)}
    unassigned = [p.id for p in cands]
    groups: dict[str, int] = {}
    seeds: list[str] = []
    while unassigned and len(seeds) < n_groups:
        seed = unassigned.pop(0)
        seeds.append(seed)
        groups[seed] = len(seeds) - 1
        near = sorted(unassigned, key=lambda pid: m[idx[seed]][idx[pid]].minutes)[: cap - 1]
        for pid in near:
            groups[pid] = groups[seed]
            unassigned.remove(pid)
    for pid in unassigned:  # leftovers join the nearest seed's group
        best = min(seeds, key=lambda s: m[idx[s]][idx[pid]].minutes)
        groups[pid] = groups[best]
    return groups


# ---- assignment ------------------------------------------------------------
def validate_assignment(out: ItinOut, cands: list[Place], dates: list[date], cap: int) -> dict[date, list[str]]:
    """Keep known IDs only, each once, at most cap per day, only on requested dates."""
    known = {p.id for p in cands}
    seen: set[str] = set()
    result: dict[date, list[str]] = {d: [] for d in dates}
    for day in out.days:
        if day.date not in result:
            continue
        for pid in day.place_ids:
            if pid in known and pid not in seen and len(result[day.date]) < cap:
                result[day.date].append(pid)
                seen.add(pid)
    return result


def fallback_assignment(cands: list[Place], groups: dict[str, int], dates: list[date],
                        cap: int) -> dict[date, list[str]]:
    result: dict[date, list[str]] = {d: [] for d in dates}
    for p in cands:  # rank order
        d = dates[groups.get(p.id, 0) % len(dates)]
        if len(result[d]) < cap:
            result[d].append(p.id)
    return result


def fill_empty_days(assign: dict[date, list[str]], cands: list[Place], cap: int) -> None:
    used = {pid for ids in assign.values() for pid in ids}
    spare = [p.id for p in cands if p.id not in used]
    for ids in assign.values():
        while not ids and spare:
            ids.extend(spare[:cap])
            del spare[:cap]


def move_outdoor_off_rain(assign: dict[date, list[str]], by_id: dict[str, Place],
                          weather: dict[date, DayWeather], cap: int) -> None:
    rainy = {d for d, w in weather.items() if is_rainy(w)}
    for d in [d for d in assign if d in rainy]:
        for pid in [p for p in assign[d] if is_outdoor(by_id[p])]:
            dry = [x for x in assign if x not in rainy and len(assign[x]) < cap]
            if dry:
                assign[d].remove(pid)
                assign[dry[0]].append(pid)


# ---- scheduling ------------------------------------------------------------
def order_nearest(ids: list[str], m: list[list[LegLite]], idx: dict[str, int]) -> list[str]:
    order, cur, left = [], 0, list(ids)
    while left:
        nxt = min(left, key=lambda pid: m[cur][idx[pid]].minutes)
        order.append(nxt)
        left.remove(nxt)
        cur = idx[nxt]
    return order


def drive_budget(pace: Pace, travel_min: int) -> int:
    """Local driving minutes allowed on a day after any intercity travel that day (never below a short loop)."""
    return max(MIN_LOCAL_DRIVE, MAX_DRIVE_MIN[pace] - travel_min)


async def schedule_day(d: date, ids: list[str], by_id: dict[str, Place], rank: dict[str, int], centre: GeoPoint,
                       win_start: int, win_end: int, extra_notes: list[str], trim: bool = True,
                       max_drive: int | None = None) -> DayPlan:
    """Route the ordered stops, compute times, and trim until the day fits its window and driving budget.

    Over the driving budget, the stop farthest from the stay goes first; otherwise the lowest-ranked one.
    trim=False (evaluation baseline) keeps an overfull day as-is so its infeasibility can be measured."""
    ids = list(ids)
    notes = list(extra_notes)
    while True:
        places = [by_id[i] for i in ids]
        legs = await route_multi([centre, *places, centre]) if places else []
        t, stops, lunch, bad = win_start, [], None, None
        for i, p in enumerate(places):
            if lunch is None and t >= LUNCH_FROM:
                lunch, t = Meal(start=hhmm(t), end=hhmm(t + LUNCH_MIN)), t + LUNCH_MIN
            t += legs[i].minutes
            hours = opening_window(p.opening_hours)
            if hours and t < hours[0]:
                t = hours[0]
            arrive, t = t, t + visit_minutes(p.kind)
            if hours and t > hours[1]:
                bad = bad or p.id
            stops.append(Stop(place_id=p.id, arrive=hhmm(arrive), depart=hhmm(t), leg_from_prev=legs[i],
                              hours_note=None if p.opening_hours else "hours not listed"))
        if places and lunch is None and t < win_end:
            ls = max(t, LUNCH_FROM)
            lunch, t = Meal(start=hhmm(ls), end=hhmm(ls + LUNCH_MIN)), ls + LUNCH_MIN
        end = t + (legs[-1].minutes if legs else 0)
        drive = sum(leg.minutes for leg in legs)
        too_far = max_drive is not None and drive > max_drive
        if (end <= win_end and bad is None and not too_far) or not ids or not trim:
            return DayPlan(date=d, window_start=hhmm(win_start), window_end=hhmm(win_end), stops=stops,
                           meals=[lunch] if lunch else [], return_leg=legs[-1] if legs else None,
                           notes=notes, end_time=hhmm(end))
        if bad:
            drop, why = bad, "closed at that time of day"
        elif too_far:
            drop = max(ids, key=lambda pid: haversine_km(centre.lat, centre.lon, by_id[pid].lat, by_id[pid].lon))
            why = "too far to fit in this day's travel time"
        else:
            drop, why = max(ids, key=lambda pid: rank.get(pid, 999)), "not enough time this day"
        ids.remove(drop)
        notes.append(f"Skipped {by_id[drop].name}: {why}")


# ---- node ------------------------------------------------------------------
def _safe_note(text: str) -> str | None:
    """LLM notes may not carry numbers (no prices, times or distances)."""
    text = text.strip()[:160]
    return text if text and not re.search(r"\d", text) else None


async def build_days(req: TripRequest, cands: list[Place], weather: list[DayWeather], centre: GeoPoint,
                     options: list[TransportOption], transport_mode: str | None, budget_note: str | None,
                     origin_name: str, road_minutes: int = 0) -> tuple[list[DayPlan], list[NodeMetric], list[str]]:
    cap = STOPS_PER_DAY[req.pace]
    chosen = choose_transport(options, transport_mode)
    travel = chosen.minutes if chosen else road_minutes
    windows = day_windows(req, travel)
    dates = [w[0] for w in windows]
    last = len(windows) - 1
    budgets = [drive_budget(req.pace, travel * ((i == 0) + (i == last))) for i in range(len(windows))]
    by_id = {p.id: p for p in cands}
    rank = {p.id: i for i, p in enumerate(cands)}
    weather_by_date = {w.date: w for w in weather}
    warnings: list[str] = []
    trace: list[NodeMetric] = []

    m = await matrix([centre, *cands]) if cands else [[LegLite(km=0, minutes=0)]]
    idx = {p.id: i + 1 for i, p in enumerate(cands)}
    groups = cluster(cands, m, len(dates), cap) if cands else {}

    payload = {
        "days": [{"date": d.isoformat(), "weather": weather_by_date[d].label if d in weather_by_date else "unknown",
                  "rainy": is_rainy(weather_by_date[d]) if d in weather_by_date else False,
                  "free_hours": round((we - ws) / 60, 1), "drive_budget_min": budgets[i]}
                 for i, (d, ws, we) in enumerate(windows)],
        "max_stops": cap, "budget_note": budget_note,
        "candidates": [{"id": p.id, "name": p.name[:60], "kind": p.kind, "outdoor": is_outdoor(p),
                        "cluster_id": groups.get(p.id, 0), "drive_min": m[0][idx[p.id]].minutes} for p in cands],
    }
    notes_by_date: dict[str, str] = {}
    try:
        out, metric = await complete_json("planner", ItinOut, prompts.ITINERARY_SYSTEM,
                                          json.dumps(payload, ensure_ascii=False), max_tokens=700, temperature=0,
                                          node="itinerary")
        trace.append(metric)
        assign = validate_assignment(out, cands, dates, cap)
        notes_by_date = out.notes
        if not any(assign.values()) and cands:
            raise ValueError("LLM assigned no valid IDs")
    except (AppError, ValueError) as e:
        log.warning("itinerary LLM unusable, deterministic fallback: %s", e)
        log.info("day grouping used the deterministic fallback")
        assign = fallback_assignment(cands, groups, dates, cap)
    fill_empty_days(assign, cands, cap)
    move_outdoor_off_rain(assign, by_id, weather_by_date, cap)

    days = []
    for i, (d, ws, we) in enumerate(windows):
        extra = []
        if note := _safe_note(notes_by_date.get(d.isoformat(), "")):
            extra.append(note)
        if travel and i == 0:
            how = f" by {chosen.mode.replace('_', ' ')}" if chosen else " by road"
            extra.append(f"Arrive from {origin_name}{how}")
        if travel and i == len(windows) - 1:
            extra.append(f"Leave in the afternoon for {origin_name}")
        ordered = order_nearest(assign[d], m, idx)
        days.append(await schedule_day(d, ordered, by_id, rank, centre, ws, we, extra, max_drive=budgets[i]))
    days = await refill_days(days, windows, budgets, cands, by_id, rank, centre, m, idx, cap)
    theme = {_safe_note(n) for n in notes_by_date.values()} - {None}
    days = [d if d.stops else d.model_copy(update={"notes": [n for n in d.notes if n not in theme]}) for d in days]
    return days, trace, warnings


async def refill_days(days: list[DayPlan], windows: list[tuple[date, int, int]], budgets: list[int],
                      cands: list[Place], by_id: dict[str, Place], rank: dict[str, int], centre: GeoPoint,
                      m: list[list[LegLite]], idx: dict[str, int], cap: int) -> list[DayPlan]:
    """Give days left short by trimming unused candidates they can actually reach, best-ranked first.

    A candidate is tried only if its round trip from the stay fits the day's driving budget, and kept only
    if the whole day still fits (no existing stop is lost)."""
    used = {s.place_id for d in days for s in d.stops}
    out = []
    for i, (day, (d, ws, we)) in enumerate(zip(days, windows, strict=True)):
        for p in cands:
            if len(day.stops) >= cap:
                break
            if p.id in used or 2 * m[0][idx[p.id]].minutes > budgets[i]:
                continue
            current = [s.place_id for s in day.stops]
            trial = await schedule_day(d, order_nearest([*current, p.id], m, idx), by_id, rank, centre, ws, we,
                                       day.notes, max_drive=budgets[i])
            if {s.place_id for s in trial.stops} == {*current, p.id}:
                stale = f"Skipped {p.name}:"  # it was trimmed from a fuller version of this day, now it fits
                day = trial.model_copy(update={"notes": [n for n in trial.notes if not n.startswith(stale)]})
                used.add(p.id)
        out.append(day)
    return out


async def itinerary_node(state: dict) -> dict:
    if state.get("days") and not state.get("replan"):
        return {}  # budget downgrade did not touch the places (e.g. cheaper stay): keep the days
    req: TripRequest = state["request"]
    dg = state.get("downgrade") or {}
    cands: list[Place] = state.get("candidates") or []
    excluded = set(state.get("excluded_ids") or [])
    cands = [p for p in cands if p.id not in excluded]
    days, trace, warnings = await build_days(
        req, cands, state.get("weather") or [], state["dest_geo"], state.get("intercity_options") or [],
        dg.get("transport_mode"), state.get("budget_cap"), state["origin_geo"].name,
        state.get("intercity_minutes", 0),
    )
    return {"days": days, "trace": trace, "warnings": warnings, "replan": False}
