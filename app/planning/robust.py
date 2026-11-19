"""Weather-robust planning: wet-day probability, per-day Plan B, and analytic expected utility.

Travel-planning benchmarks and solvers (TravelPlanner, LLM-Modulo, SMT/MILP planners) treat the world
as deterministic. Here every day carries p_wet, the probability it is a rainy day:
  * inside the forecast window: Open-Meteo's precipitation probability;
  * beyond it: climatology, the share of days within +/-7 calendar days over 10 years with
    >= 2.5 mm of rain (the India Meteorological Department's definition of a "rainy day").
Each day with outdoor stops gets a Plan B: the best feasible indoor day built from that day's indoor
stops plus places unused anywhere in the plan. The traveller switches on a wet morning, so the
expected value of a day is (1-p)*U(A) + p*max(U_wet(A), U(B)).
"""
from dataclasses import dataclass
from datetime import date, timedelta

from app.planning.model import Instance, best_order
from app.planning.solver import RAIN_LOSS, TIME_BUDGET_S, solve

WET_MM = 2.5  # IMD rainy-day threshold
WINDOW_DAYS = 7


def p_wet_climatology(series: dict[str, float], d: date) -> float:
    """Share of rainy days within +/-WINDOW_DAYS of this calendar date across the series' years."""
    hits = total = 0
    for year in {int(k[:4]) for k in series}:
        try:
            centre = d.replace(year=year)
        except ValueError:
            centre = d.replace(year=year, day=28)
        for k in range(-WINDOW_DAYS, WINDOW_DAYS + 1):
            v = series.get((centre + timedelta(days=k)).isoformat())
            if v is not None:
                total += 1
                hits += v >= WET_MM
    return hits / total if total else 0.0


def p_wet_from_forecast(precip_prob: int | None) -> float:
    return (precip_prob or 0) / 100


@dataclass
class DayValue:
    dry: float  # utility of plan A on a dry day
    wet_a: float  # plan A on a wet day (outdoor stops lose RAIN_LOSS)
    plan_b: list[int]  # ordered indoor fallback (may be empty)
    wet_b: float


def day_utility(inst: Instance, ids: list[int], wet: bool, rain_loss: float = RAIN_LOSS) -> float:
    return sum(inst.util[i] * (1 - rain_loss if (wet and inst.outdoor[i]) else 1) for i in ids)


def plan_b(inst: Instance, days: list[list[int]], reserved: list[list[int]] | None = None) -> list[list[int]]:
    """Indoor fallback per day: the day's own indoor stops, the places the master reserved for it, and any
    place unused elsewhere. A fallback never takes another day's stops or reservations, nor another
    fallback's places, so a run of wet days stays covered."""
    reserved = reserved or [[] for _ in days]
    taken = {i for o in days for i in o} | {i for r in reserved for i in r}
    out = []
    for d, order in enumerate(days):
        if not any(inst.outdoor[i] for i in order):
            out.append([])
            continue
        pool = ({i for i in order if not inst.outdoor[i]} | set(reserved[d])
                | {i for i in range(inst.n) if i not in taken and not inst.outdoor[i]})
        sub = Instance(inst.req, inst.places, inst.util, inst.m, [inst.days[d]], inst.cap,
                       inst.outdoor, inst.visit, inst.hours)
        res = solve(sub, robust=False, allowed=[pool], max_iters=60, cover=False)
        b = res.days[0]
        out.append(b)
        taken |= set(b)
    return out


def evaluate(inst: Instance, days: list[list[int]], backups: list[list[int]] | None = None,
             rain_loss: float = RAIN_LOSS) -> dict:
    """Expected, dry-world and all-wet utility of a plan (analytic, no sampling)."""
    backups = backups or [[] for _ in days]
    exp = dry = worst = 0.0
    for d, order in enumerate(days):
        p = inst.days[d].p_wet
        u_dry = day_utility(inst, order, False)
        u_wet = max(day_utility(inst, order, True, rain_loss), day_utility(inst, backups[d], True, rain_loss))
        exp += (1 - p) * u_dry + p * u_wet
        dry += u_dry
        worst += u_wet
    return {"expected": exp, "dry": dry, "all_wet": worst}


def solve_robust(inst: Instance, rain_loss: float = RAIN_LOSS, time_budget: float = TIME_BUDGET_S):
    """Plan A by expected utility, then Plan B per day. Returns (SolveResult, backups)."""
    res = solve(inst, robust=True, rain_loss=rain_loss, time_budget=time_budget)
    return res, plan_b(inst, res.days, res.reserved)


__all__ = ["best_order", "evaluate", "p_wet_climatology", "p_wet_from_forecast", "plan_b", "solve_robust"]
