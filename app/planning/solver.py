"""Exact day planning by logic-based Benders decomposition (Hooker & Ottosson, 2003).

Master (MILP, HiGHS via scipy): choose which place goes on which day to maximise (expected) utility,
under a *relaxed* time and driving model. Subproblem: the exact day verifier (`model.best_order`).
Every day the verifier rejects yields a no-good cut on a minimal infeasible subset of that day's
places. The master never cuts off a feasible plan, so when the loop stops with every day verified,
the plan is optimal for the model (certificate: master objective == plan objective).

The LLM never touches this step: it only proposed the candidate pool upstream ("LLM proposes,
solver disposes", in the spirit of LLM-Modulo, Kambhampati et al. 2024).
"""
import time
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

from app.agents.itinerary import LUNCH_MIN
from app.planning.model import DayCtx, Instance, best_order, feasible

WET_DAY = 0.5  # same threshold as the Weather Safety critic
PENALTY = 1000.0  # objective cost of an uncovered interest or a wet outdoor day without a fallback
RAIN_LOSS = 0.7  # share of an outdoor stop's value lost when the day turns out rainy (sensitivity in paper)
MAX_ITERS = 400
TIME_BUDGET_S = 60.0  # wall-clock cap; past it the last master plan is repaired and reported as not optimal


@dataclass
class SolveResult:
    days: list[list[int]]  # ordered place indices per day
    objective: float
    bound: float  # master objective at termination (upper bound on any feasible plan)
    iterations: int
    cuts: int
    seconds: float
    optimal: bool
    log: list[str] = field(default_factory=list)
    reserved: list[list[int]] = field(default_factory=list)  # indoor fallbacks reserved per day (robust)


def weights(inst: Instance, robust: bool, rain_loss: float = RAIN_LOSS) -> np.ndarray:
    """w[i, d]: utility of place i on day d; robust = expected utility under the day's wet probability."""
    w = np.zeros((inst.n, len(inst.days)))
    for i in range(inst.n):
        for d, day in enumerate(inst.days):
            loss = rain_loss * day.p_wet if (robust and inst.outdoor[i]) else 0.0
            w[i, d] = inst.util[i] * (1 - loss) - 1e-4 * inst.leg(None, i)  # tiny tie-break: closer first
    return w


def _min_in(inst: Instance, i: int) -> int:
    """Lower bound on the leg into i in any route (from the stay or another place)."""
    return min(inst.leg(j, i) for j in [None, *range(inst.n)] if j != i)


def _fits_alone(inst: Instance, d: int, i: int) -> bool:
    """Day-trip reach (direct round trip within the driving budget) and the place alone fits the day."""
    day = inst.days[d]
    return inst.leg(None, i) + inst.leg(i, None) <= day.max_drive and feasible(inst, day, [i])


def _minimal_infeasible(inst: Instance, day: DayCtx, ids: list[int], util: list[float]) -> list[int]:
    """Shrink an infeasible set while it stays infeasible (deletion filter), dropping high-value items first
    so the cut is tight around what actually breaks the day."""
    core = list(ids)
    for i in sorted(ids, key=lambda k: -util[k]):
        trial = [k for k in core if k != i]
        if trial and not feasible(inst, day, trial):
            core = trial
    return core


def solve(inst: Instance, robust: bool = True, rain_loss: float = RAIN_LOSS,
          allowed: list[set[int]] | None = None, max_iters: int = MAX_ITERS,
          cover: bool = True, time_budget: float = TIME_BUDGET_S) -> SolveResult:
    """Variables: x[i,d] place i on day d (plan A); with robust, r[i,d] indoor place i reserved as day d's
    rain fallback and o[d] "day d has an outdoor stop"; slack variables keep the master feasible when an
    interest cannot be covered (s_cov) or a wet outdoor day cannot get a fallback (s_wet), at a large penalty.
    The critic bank's hard constraints are part of the model."""
    t0 = time.perf_counter()
    n, D = inst.n, len(inst.days)
    if n == 0:
        return SolveResult([[] for _ in range(D)], 0.0, 0.0, 0, 0, 0.0, True)
    w = weights(inst, robust, rain_loss)
    wanted = sorted({x.value for x in inst.req.interests}) if cover else []
    reach = [[_fits_alone(inst, d, i) and not (allowed and i not in allowed[d]) for d in range(D)]
             for i in range(n)]
    wet_days = [d for d, day in enumerate(inst.days) if robust and day.p_wet >= WET_DAY
                and any(not inst.outdoor[i] and reach[i][d] for i in range(n))]
    covers = [k for k in wanted if any(k in inst.places[i].interests and any(reach[i]) for i in range(n))]
    R = n * D  # offsets of each variable block
    OD = R + (n * D if wet_days else 0)
    SC = OD + (D if wet_days else 0)
    SW = SC + len(covers)
    nv = SW + (D if wet_days else 0)

    def v(i: int, d: int) -> int:
        return i * D + d

    def rv(i: int, d: int) -> int:
        return R + i * D + d

    c = np.zeros(nv)
    ub = np.zeros(nv)
    for i in range(n):
        for d in range(D):
            c[v(i, d)] = -w[i, d]
            ub[v(i, d)] = 1.0 if reach[i][d] else 0.0
            if d in wet_days and not inst.outdoor[i] and reach[i][d]:
                ub[rv(i, d)] = 1.0
                c[rv(i, d)] = -0.5 * inst.days[d].p_wet * inst.util[i]  # a reserved fallback has some value
    for d in wet_days:
        ub[OD + d] = 1.0
        ub[SW + d], c[SW + d] = 1.0, PENALTY
    for k in range(len(covers)):
        ub[SC + k], c[SC + k] = 1.0, PENALTY
    rows, lo, hi = [], [], []

    def add(coefs: dict[int, float], lower: float, upper: float) -> None:
        rows.append(coefs)
        lo.append(lower)
        hi.append(upper)

    for i in range(n):  # each place at most once, as a stop or as a reserved fallback
        add({**{v(i, d): 1 for d in range(D)}, **{rv(i, d): 1 for d in wet_days}}, 0, 1)
    min_in = [_min_in(inst, i) for i in range(n)]
    for d, day in enumerate(inst.days):
        # Valid relaxations of the day verifier: every leg into a stop is at least its shortest inbound leg,
        # a non-empty feasible day always contains the lunch break and ends with a return leg at least as
        # long as the shortest way back to the stay from any place that can be on this day.
        back = min((inst.leg(i, None) for i in range(n) if reach[i][d]), default=0)
        add({v(i, d): 1 for i in range(n)}, 0, inst.cap)  # pace
        add({v(i, d): inst.visit[i] + min_in[i] for i in range(n)}, 0,
            max(0, day.end - day.start - LUNCH_MIN - back))  # time (relaxed)
        add({v(i, d): min_in[i] for i in range(n)}, 0, max(0, day.max_drive - back))  # driving (relaxed)
    for d in wet_days:  # weather safety: an outdoor stop on a wet day needs a reserved indoor fallback
        for i in range(n):
            if inst.outdoor[i]:
                add({OD + d: 1, v(i, d): -1}, 0, 1)  # o[d] >= x[i,d]
        add({**{rv(i, d): 1 for i in range(n)}, OD + d: -1, SW + d: 1}, 0, np.inf)  # sum_i r[i,d] >= o[d] - s
    for k, interest in enumerate(covers):  # interest coverage
        add({**{v(i, d): 1 for i in range(n) if interest in inst.places[i].interests for d in range(D)},
             SC + k: 1}, 1, np.inf)
    # Days with the same window and driving budget have the same feasible sets, so a cut learned on one
    # holds on all of them (exact lifting).
    sig = [(o.start, o.end, o.max_drive) for o in inst.days]
    twins = [[e for e in range(D) if sig[e] == sig[d]] for d in range(D)]
    pre = 0
    for d in range(D):  # pair pre-cuts: every infeasible pair of reachable places, found up front
        if twins[d][0] != d:
            continue
        ok = [i for i in range(n) if reach[i][d]]
        for a in range(len(ok)):
            for b in range(a + 1, len(ok)):
                i, j = ok[a], ok[b]
                if not feasible(inst, inst.days[d], [i, j]):
                    for e in twins[d]:
                        add({v(i, e): 1, v(j, e): 1}, 0, 1)
                    pre += 1

    log, cuts, it, plan, bound = [f"pair pre-cuts: {pre}"], 0, 0, [[] for _ in range(D)], 0.0
    reserved: list[list[int]] = [[] for _ in range(D)]
    while it < max_iters and time.perf_counter() - t0 < time_budget:
        it += 1
        A = lil_matrix((len(rows), nv))
        for r, coefs in enumerate(rows):
            for j, val in coefs.items():
                A[r, j] = val
        res = milp(c, integrality=np.ones(nv), bounds=Bounds(np.zeros(nv), ub),
                   constraints=LinearConstraint(A.tocsr(), lo, hi), options={"time_limit": 10})
        if res.x is None:
            log.append(f"iter {it}: master failed ({res.message})")
            break
        xs = np.round(res.x)
        bound = -res.fun
        new_cuts = 0
        plan = []
        for d, day in enumerate(inst.days):
            ids = [i for i in range(n) if xs[v(i, d)] > 0.5]
            order, r = best_order(inst, day, ids)
            if not r.ok:
                core = _minimal_infeasible(inst, day, ids, inst.util)
                for e in twins[d]:
                    add({v(i, e): 1 for i in core}, 0, len(core) - 1)
                new_cuts += 1
            plan.append(order)
        reserved = [[i for i in range(n) if d in wet_days and xs[rv(i, d)] > 0.5] for d in range(D)]
        cuts += new_cuts
        log.append(f"iter {it}: master={bound:.3f} cuts+={new_cuts}")
        if new_cuts == 0:
            return SolveResult(plan, bound, bound, it, cuts, time.perf_counter() - t0, True, log, reserved)
    # out of iterations: repair the last master plan greedily so we still deliver a feasible plan
    fixed = []
    for d, day in enumerate(inst.days):
        ids = sorted(plan[d] if d < len(plan) else [], key=lambda i: -w[i, d])
        while ids and not feasible(inst, day, ids):
            ids.pop()
        fixed.append(best_order(inst, day, ids)[0])
    obj = float(sum(w[i, d] for d, o in enumerate(fixed) for i in o))
    return SolveResult(fixed, obj, bound, it, cuts, time.perf_counter() - t0, False, log, reserved)
