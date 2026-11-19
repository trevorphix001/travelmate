"""Critic bank: TravelPlanner's constraint evaluator, ported to day-trip itineraries.

Structure, return convention ((passed | None, message)) and the aggregate metrics (Delivery Rate,
Commonsense/Hard Micro and Macro Pass Rate, Final Pass Rate) follow
OSU-NLP-Group/TravelPlanner evaluation/{commonsense_constraint,hard_constraint,eval}.py
(Xie et al., ICML 2024; MIT licence). Constraint names keep the paper's terms where the meaning carries
over and add the realism and weather constraints this domain needs.

The same critics serve two roles: an evaluator (paper tables) and, as in LLM-Modulo
(Kambhampati et al., ICML 2024; Gundawar et al., 2024), a source of back-prompts for an LLM planner.
"""
from app.planning.model import Instance, feasible, simulate

Check = tuple[bool | None, str | None]
WET_DAY = 0.5  # a day more likely wet than not
COMMONSENSE = ["Within Sandbox", "Within Current City", "Diverse Attractions", "Complete Information",
               "Day Window", "Drive Limit", "Opening Hours"]
HARD = ["Pace", "Interest Coverage", "Weather Safety"]


def _hhmm(t: int) -> str:
    return f"{t // 60:02d}:{t % 60:02d}"


def _fits_alone(inst: Instance, d: int, i: int) -> bool:
    """Place i alone is a feasible day d (day-trip reach + exact day verifier)."""
    return inst.leg(None, i) + inst.leg(i, None) <= inst.days[d].max_drive and feasible(inst, inst.days[d], [i])


# ---- commonsense ------------------------------------------------------------
def within_sandbox(inst: Instance, plan: list[list], known: set) -> Check:
    bad = [pid for day in plan for pid in day if pid not in known]
    return (False, f"Places {bad[:3]} are not in the tool results; use only candidate IDs.") if bad else (True, None)


def within_current_city(inst: Instance, plan: list[list[int]]) -> Check:
    for d, day in enumerate(plan):
        for i in day:
            if inst.leg(None, i) + inst.leg(i, None) > inst.days[d].max_drive:
                return False, (f"Day {d + 1}: {inst.places[i].name} is beyond a day trip from the stay "
                               f"({inst.leg(None, i)} min each way, budget {inst.days[d].max_drive} min).")
    return True, None


def diverse_attractions(inst: Instance, plan: list[list[int]]) -> Check:
    seen: set[int] = set()
    for d, day in enumerate(plan):
        for i in day:
            if i in seen:
                return False, f"The attraction '{inst.places[i].name}' in day {d + 1} is repeated."
            seen.add(i)
    return True, None


def complete_information(inst: Instance, plan: list[list[int]]) -> Check:
    if len(plan) != len(inst.days):
        return False, f"The plan has {len(plan)} days; the trip has {len(inst.days)}."
    used = {i for day in plan for i in day}
    for d, day in enumerate(plan):
        if not day and any(i not in used and _fits_alone(inst, d, i) for i in range(inst.n)):
            return False, f"Day {d + 1} has no stops although an unused place fits that day."
    return True, None


def _per_day(inst: Instance, plan: list[list[int]], what: str) -> Check:
    for d, day in enumerate(plan):
        r = simulate(inst, inst.days[d], day)
        if what == "window" and r.late:
            return False, (f"Day {d + 1} ends at {_hhmm(r.end)}, after {_hhmm(inst.days[d].end)}; "
                           f"remove or swap a stop.")
        if what == "drive" and r.too_far:
            return False, (f"Day {d + 1} needs {r.drive} min of driving; the limit is {inst.days[d].max_drive}. "
                           f"Group nearby places on the same day.")
        if what == "hours" and r.closed is not None:
            return False, f"Day {d + 1}: {inst.places[r.closed].name} is closed when the plan visits it."
    return True, None


# ---- hard -----------------------------------------------------------------
def pace(inst: Instance, plan: list[list[int]]) -> Check:
    over = [d + 1 for d, day in enumerate(plan) if len(day) > inst.cap]
    return (False, f"Days {over} exceed {inst.cap} stops for this pace.") if over else (True, None)


def interest_coverage(inst: Instance, plan: list[list[int]]) -> Check:
    wanted = {i.value for i in inst.req.interests}
    reachable = {x for i in range(inst.n) if inst.leg(None, i) * 2 <= max(d.max_drive for d in inst.days)
                 for x in inst.places[i].interests}
    covered = {x for day in plan for i in day for x in inst.places[i].interests}
    missing = (wanted & reachable) - covered
    if not wanted & reachable:
        return None, None  # nothing to cover: constraint not applicable (TravelPlanner returns None too)
    return (False, f"No stop covers the interests {sorted(missing)}.") if missing else (True, None)


def weather_safety(inst: Instance, plan: list[list[int]], backups: list[list[int]] | None = None) -> Check:
    """On a likely-wet day, outdoor stops need an indoor Plan B, unless no indoor place can fit that day."""
    wet = [d for d, day in enumerate(plan) if inst.days[d].p_wet >= WET_DAY and any(inst.outdoor[i] for i in day)]
    applicable = [d for d in wet if any(not inst.outdoor[i] and _fits_alone(inst, d, i) for i in range(inst.n))]
    if not applicable:
        return None, None
    for d in applicable:
        if not (backups and d < len(backups) and backups[d]):
            return False, (f"Day {d + 1} is likely rainy (p={inst.days[d].p_wet:.2f}) but is planned outdoors "
                           f"with no indoor fallback; move outdoor stops to a drier day.")
    return True, None


# ---- aggregate --------------------------------------------------------------
def evaluate(inst: Instance, plan: list[list[int]], backups: list[list[int]] | None = None,
             raw_ids: list[list] | None = None) -> dict[str, dict[str, Check]]:
    """plan uses place indices; raw_ids (optional) is the planner's unvalidated output, for the sandbox check."""
    known = {p.id for p in inst.places}
    raw = raw_ids if raw_ids is not None else [[inst.places[i].id for i in day] for day in plan]
    return {
        "commonsense": {
            "Within Sandbox": within_sandbox(inst, raw, known),
            "Within Current City": within_current_city(inst, plan),
            "Diverse Attractions": diverse_attractions(inst, plan),
            "Complete Information": complete_information(inst, plan),
            "Day Window": _per_day(inst, plan, "window"),
            "Drive Limit": _per_day(inst, plan, "drive"),
            "Opening Hours": _per_day(inst, plan, "hours"),
        },
        "hard": {"Pace": pace(inst, plan), "Interest Coverage": interest_coverage(inst, plan),
                 "Weather Safety": weather_safety(inst, plan, backups)},
    }


def feedback(result: dict[str, dict[str, Check]]) -> list[str]:
    """Critic messages for a back-prompt (LLM-Modulo)."""
    return [f"[{name}] {msg}" for group in result.values() for name, (ok, msg) in group.items() if ok is False]


def passed(result: dict[str, dict[str, Check]], group: str | None = None) -> bool:
    groups = [group] if group else list(result)
    return all(ok is not False for g in groups for ok, _ in result[g].values())


def pass_rates(results: list[dict | None]) -> dict[str, float]:
    """TravelPlanner metrics. None = no plan delivered. Micro: share of applicable constraints passed."""
    n = len(results) or 1
    out = {"Delivery Rate": sum(r is not None for r in results) / n}
    for g, label in (("commonsense", "Commonsense"), ("hard", "Hard")):
        num = den = 0
        for r in results:
            names = COMMONSENSE if g == "commonsense" else HARD
            for name in names:
                ok = r[g][name][0] if r else False
                if ok is None:
                    continue
                den += 1
                num += bool(ok)
        out[f"{label} Micro Pass Rate"] = num / den if den else 0.0
        out[f"{label} Macro Pass Rate"] = sum(bool(r) and passed(r, g) for r in results) / n
    out["Final Pass Rate"] = sum(bool(r) and passed(r) for r in results) / n
    return out
