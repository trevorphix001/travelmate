"""Planning instance and exact day verifier shared by the solver, the baselines and the critic bank.

A day is verified by simulating it minute by minute with the *same* rules as
`app.agents.itinerary.schedule_day` (lunch from 12:30, opening hours, visit durations, return leg,
day window, daily driving budget), but on the travel-time matrix the planner already holds, so it is
pure, fast and deterministic.
"""
from dataclasses import dataclass, field
from datetime import date
from itertools import permutations

from app.agents.durations import visit_minutes
from app.agents.itinerary import (
    LUNCH_FROM,
    LUNCH_MIN,
    STOPS_PER_DAY,
    day_windows,
    drive_budget,
    is_outdoor,
    opening_window,
)
from app.schemas.place import Place
from app.schemas.trip import TripRequest

EXACT_ORDER_MAX = 7  # exact ordering by enumeration up to 7 stops (5040 orders); above that, 2-opt


@dataclass
class DayCtx:
    date: date
    start: int  # minutes after midnight
    end: int
    max_drive: int
    p_wet: float = 0.0  # probability the day is a rainy day (see app.planning.rain)


@dataclass
class Instance:
    req: TripRequest
    places: list[Place]
    util: list[float]  # utility of visiting place i (interest match, notability, distance)
    m: list[list[int]]  # travel minutes, index 0 = stay/centre, i+1 = places[i]
    days: list[DayCtx]
    cap: int
    outdoor: list[bool] = field(default_factory=list)
    visit: list[int] = field(default_factory=list)
    hours: list[tuple[int, int] | None] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.outdoor = self.outdoor or [is_outdoor(p) for p in self.places]
        self.visit = self.visit or [visit_minutes(p.kind) for p in self.places]
        self.hours = self.hours or [opening_window(p.opening_hours) for p in self.places]

    @property
    def n(self) -> int:
        return len(self.places)

    def leg(self, a: int | None, b: int | None) -> int:
        """Minutes from place a to place b; None is the stay/centre."""
        return self.m[0 if a is None else a + 1][0 if b is None else b + 1]


@dataclass
class DayResult:
    ok: bool
    end: int
    drive: int
    late: bool  # ends after the window
    too_far: bool  # over the driving budget
    closed: int | None  # first place visited outside its opening hours
    times: list[tuple[int, int]]  # (arrive, depart) per stop


def make_days(req: TripRequest, intercity_min: int, p_wet: list[float] | None = None) -> list[DayCtx]:
    wins = day_windows(req, intercity_min)
    last = len(wins) - 1
    out = []
    for i, (d, s, e) in enumerate(wins):
        travel = intercity_min * ((i == 0) + (i == last))
        out.append(DayCtx(d, s, e, drive_budget(req.pace, travel), (p_wet or [0.0] * len(wins))[i]))
    return out


def cap_for(req: TripRequest) -> int:
    return STOPS_PER_DAY[req.pace]


def simulate(inst: Instance, day: DayCtx, order: list[int]) -> DayResult:
    """Same timeline rules as schedule_day, on the matrix."""
    t, prev, lunch, closed, drive, times = day.start, None, False, None, 0, []
    for i in order:
        if not lunch and t >= LUNCH_FROM:
            lunch, t = True, t + LUNCH_MIN
        leg = inst.leg(prev, i)
        t += leg
        drive += leg
        h = inst.hours[i]
        if h and t < h[0]:
            t = h[0]
        arrive, t = t, t + inst.visit[i]
        if h and t > h[1] and closed is None:
            closed = i
        times.append((arrive, t))
        prev = i
    if order and not lunch and t < day.end:
        t = max(t, LUNCH_FROM) + LUNCH_MIN
    back = inst.leg(prev, None) if order else 0
    end, drive = t + back, drive + back
    late, too_far = end > day.end, drive > day.max_drive
    return DayResult(not (late or too_far or closed is not None) or not order, end, drive, late, too_far,
                     closed, times)


def _two_opt(inst: Instance, order: list[int]) -> list[int]:
    def length(o: list[int]) -> int:
        return sum(inst.leg(a, b) for a, b in zip([None, *o], [*o, None], strict=True))

    best, improved = list(order), True
    while improved:
        improved = False
        for i in range(len(best) - 1):
            for j in range(i + 1, len(best)):
                cand = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                if length(cand) < length(best):
                    best, improved = cand, True
    return best


def best_order(inst: Instance, day: DayCtx, ids: list[int]) -> tuple[list[int], DayResult]:
    """Feasible order that finishes earliest (exact for <= EXACT_ORDER_MAX stops); else the best infeasible one."""
    if not ids:
        return [], simulate(inst, day, [])
    if len(ids) <= EXACT_ORDER_MAX:
        best: tuple[list[int], DayResult] | None = None
        for p in permutations(ids):
            r = simulate(inst, day, list(p))
            key = (not r.ok, r.end)
            if best is None or key < (not best[1].ok, best[1].end):
                best = (list(p), r)
        return best  # type: ignore[return-value]
    o = _two_opt(inst, sorted(ids, key=lambda i: inst.leg(None, i)))
    return o, simulate(inst, day, o)


def feasible(inst: Instance, day: DayCtx, ids: list[int]) -> bool:
    return best_order(inst, day, ids)[1].ok
