"""Robust planner: Benders MILP optimality, Plan B, climatology, critics and TravelPlanner-style metrics."""
from datetime import date
from itertools import combinations

from app.planning import critics
from app.planning.model import DayCtx, Instance, best_order, feasible
from app.planning.robust import evaluate, p_wet_climatology, plan_b, solve_robust
from app.planning.solver import solve
from app.schemas.place import Confidence, Place, Source
from app.schemas.trip import Pace, TripRequest

SRC = Source(name="test")


def place(i: int, kind: str, interests=("nature",)) -> Place:
    return Place(id=f"osm:node/{i}", name=f"P{i}", kind=kind, interests=list(interests), lat=25.5, lon=91.9,
                 source=SRC, confidence=Confidence.verified)


def instance(p_wet=(0.0, 0.0), minutes=20) -> Instance:
    req = TripRequest(origin="Guwahati", destination="Shillong", start_date=date(2026, 12, 10),
                      end_date=date(2026, 12, 9 + len(p_wet)), travellers=2, budget_inr=9000,
                      interests=["nature", "heritage"], pace=Pace.normal)
    places = [place(0, "waterfall"), place(1, "viewpoint"), place(2, "museum", ["heritage"]),
              place(3, "park"), place(4, "museum", ["heritage"]), place(5, "waterfall")]
    n = len(places)
    m = [[0 if a == b else minutes for b in range(n + 1)] for a in range(n + 1)]
    for k in range(n + 1):  # place 5 is a long way from the stay and from everything else
        if k != 6:
            m[k][6] = m[6][k] = 200
    days = [DayCtx(req.start_date.replace(day=10 + i), 9 * 60, 20 * 60, 240, p) for i, p in enumerate(p_wet)]
    return Instance(req, places, [5, 4, 3, 2, 1, 9], m, days, cap=2)


def brute_force(inst: Instance) -> float:
    """Optimal plan value by enumeration (tiny instance) to certify the Benders loop."""
    def rec(d: int, used: set[int]) -> float:
        if d == len(inst.days):
            return 0.0
        out = 0.0
        free = [i for i in range(inst.n) if i not in used]
        for k in range(inst.cap + 1):
            for s in combinations(free, k):
                reach = all(2 * inst.leg(None, i) <= inst.days[d].max_drive for i in s)  # day-trip rule
                if reach and feasible(inst, inst.days[d], list(s)):
                    out = max(out, sum(inst.util[i] for i in s) + rec(d + 1, used | set(s)))
        return out

    return rec(0, set())


def test_benders_matches_brute_force_and_is_feasible():
    inst = instance()
    res = solve(inst, robust=False)
    assert res.optimal
    assert abs(sum(inst.util[i] for o in res.days for i in o) - brute_force(inst)) < 1e-6
    assert all(best_order(inst, inst.days[d], o)[1].ok for d, o in enumerate(res.days))
    assert 5 not in {i for o in res.days for i in o}  # 400-minute round trip > 240-minute drive budget


def test_robust_moves_outdoor_off_wet_day_and_adds_plan_b():
    inst = instance(p_wet=(0.9, 0.0))
    res, backups = solve_robust(inst)
    wet_outdoor = any(inst.outdoor[i] for i in res.days[0])
    assert not wet_outdoor or backups[0]  # weather safety: outdoors on a wet day only with an indoor Plan B
    assert all(not inst.outdoor[i] for i in backups[0])
    assert critics.passed(critics.evaluate(inst, res.days, backups))
    nominal = solve(inst, robust=False).days
    assert evaluate(inst, res.days, backups)["expected"] >= evaluate(inst, nominal)["expected"] - 1e-9


def test_plan_b_is_indoor_and_unused():
    inst = instance(p_wet=(0.6, 0.6))
    days = [[0, 1], [3]]
    b = plan_b(inst, days)
    assert all(not inst.outdoor[i] for o in b for i in o)
    assert not ({i for o in b for i in o} & {0, 1, 3})


def test_climatology_counts_imd_rainy_days():
    series = {"2020-07-09": 0.0, "2020-07-10": 10.0, "2021-07-10": 3.0, "2021-07-11": 1.0}
    assert p_wet_climatology(series, date(2026, 7, 10)) == 0.5


def test_critics_flag_violations_and_pass_rates():
    inst = instance(p_wet=(0.8, 0.0))
    bad = [[0, 0, 1], []]  # repeat, overfull, outdoors on a wet day with no Plan B, empty day
    ev = critics.evaluate(inst, bad, raw_ids=[["osm:node/0", "invented"], []])
    assert ev["commonsense"]["Within Sandbox"][0] is False
    assert ev["commonsense"]["Diverse Attractions"][0] is False
    assert ev["commonsense"]["Complete Information"][0] is False
    assert ev["hard"]["Pace"][0] is False and ev["hard"]["Weather Safety"][0] is False
    assert critics.feedback(ev)
    res, backups = solve_robust(inst)
    good = critics.evaluate(inst, res.days, backups)
    assert critics.passed(good)
    rates = critics.pass_rates([ev, good, None])
    assert rates["Delivery Rate"] == 2 / 3 and rates["Final Pass Rate"] == 1 / 3
