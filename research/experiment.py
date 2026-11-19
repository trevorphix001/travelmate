"""Offline experiment on frozen tool outputs: six planners, same inputs, TravelPlanner-style metrics.

    python -m research.experiment                    # solver + greedy systems only (no LLM, seconds)
    python -m research.experiment --llm --provider groq   # also the three LLM systems

Systems
  greedy        TravelMate's deterministic fallback: cluster -> assign -> rain shuffle -> trim -> refill
  llm_pipeline  TravelMate today: LLM groups IDs -> same Python repair (trim, refill)
  llm_raw       the LLM's grouping alone, ordered optimally, no repair
  llm_modulo    llm_raw + critic back-prompts, up to 3 rounds (LLM-Modulo, Gundawar et al. 2024)
  milp          Benders MILP, weather-agnostic objective (ours, ablation)
  milp_robust   Benders MILP, expected-utility objective + per-day indoor Plan B (ours, full)

Two weather regimes per case: the eval dates (Nov-Dec, mostly dry) and the same trip shifted to July
(monsoon), with climatological wet-day probabilities from 10 years of Open-Meteo archive data.
"""
import argparse
import asyncio
import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path

from app.agents.itinerary import (
    ItinOut,
    cluster,
    fallback_assignment,
    fill_empty_days,
    move_outdoor_off_rain,
    order_nearest,
    validate_assignment,
)
from app.agents.research import km_from, score
from app.core.config import get_settings
from app.llm import prompts
from app.llm.client import complete_json
from app.planning import critics
from app.planning.model import Instance, best_order, cap_for, make_days, simulate
from app.planning.robust import evaluate, p_wet_climatology, solve_robust
from app.planning.solver import solve
from app.schemas.place import Confidence, GeoPoint, Place, Source
from app.schemas.plan import DayWeather
from app.schemas.trip import TripRequest
from app.tools.routing import LegLite

log = logging.getLogger("experiment")
SNAP = Path("research/data/snapshots")
OUT = Path("research/results")
MODULO_ROUNDS = 3
LLM_RETRIES = 4
CONCURRENCY = 4
SYSTEMS = ["greedy", "llm_pipeline", "llm_raw", "llm_modulo", "milp", "milp_robust"]


# ---- instance -------------------------------------------------------------
def utility(p: Place, req: TripRequest, dest: GeoPoint) -> float:
    """The Research agent's own deterministic score, floored so every place is worth something."""
    return round(max(0.5, score(p, req, km_from(p, dest))), 3)


def load_instance(path: Path, season: str, series: dict) -> tuple[Instance, dict]:
    s = json.loads(path.read_text(encoding="utf-8"))
    req = TripRequest.model_validate(s["request"])
    if season == "monsoon":
        shift = date(req.start_date.year + (req.start_date.month > 7), 7, 10) - req.start_date
        req = req.model_copy(update={"start_date": req.start_date + shift, "end_date": req.end_date + shift})
    dest = GeoPoint.model_validate(s["dest"])
    places = [Place.model_validate(p) for p in s["pool"]]
    m = [[c[1] for c in row] for row in s["matrix"]]
    rain = s.get("precip") or series.get(s["rain_key"], {})
    n_days = (req.end_date - req.start_date).days + 1
    p = [round(p_wet_climatology(rain, req.start_date + timedelta(days=i)), 3) for i in range(n_days)]
    days = make_days(req, s["intercity"]["minutes"] if s["intercity"]["km"] >= 10 else 0, p)
    inst = Instance(req, places, [utility(x, req, dest) for x in places], m, days, cap_for(req))
    return inst, s


# ---- TravelMate's own repair (schedule_day / refill_days on the matrix) ----------------------
def trim(inst: Instance, d: int, order: list[int], rank: dict[int, int]) -> list[int]:
    ids, day = list(order), inst.days[d]
    while ids:
        r = simulate(inst, day, ids)
        if r.ok:
            break
        if r.closed is not None:
            drop = r.closed
        elif r.too_far:
            drop = max(ids, key=lambda i: inst.leg(None, i))  # farthest from the stay
        else:
            drop = max(ids, key=lambda i: rank[i])
        ids.remove(drop)
    return ids


def repair(inst: Instance, assign: list[list[int]], rank: dict[int, int], mm, idx) -> list[list[int]]:
    ids_of = {i: inst.places[i].id for i in range(inst.n)}
    back = {v: k for k, v in ids_of.items()}
    days = []
    for d, ids in enumerate(assign):
        ordered = [back[x] for x in order_nearest([ids_of[i] for i in ids], mm, idx)]
        days.append(trim(inst, d, ordered, rank))
    used = {i for o in days for i in o}
    for d in range(len(days)):  # refill_days
        for i in sorted(range(inst.n), key=lambda k: rank[k]):
            if len(days[d]) >= inst.cap:
                break
            if i in used or 2 * inst.leg(None, i) > inst.days[d].max_drive:
                continue
            trial_ids = [back[x] for x in order_nearest([ids_of[k] for k in [*days[d], i]], mm, idx)]
            trial = trim(inst, d, trial_ids, rank)
            if set(trial) == {*days[d], i}:
                days[d] = trial
                used.add(i)
    return days


def _legs(inst: Instance):
    mm = [[LegLite(km=0, minutes=v) for v in row] for row in inst.m]
    idx = {p.id: i + 1 for i, p in enumerate(inst.places)}
    return mm, idx


def _weather(inst: Instance) -> dict:
    src = Source(name="climatology")
    return {d.date: DayWeather(date=d.date, kind="forecast", label="Typical", precip_prob=round(d.p_wet * 100),
                               source=src, confidence=Confidence.estimated) for d in inst.days}


def to_idx(inst: Instance, assign: dict) -> list[list[int]]:
    pos = {p.id: i for i, p in enumerate(inst.places)}
    return [[pos[x] for x in assign[d.date] if x in pos] for d in inst.days]


def greedy(inst: Instance) -> list[list[int]]:
    mm, idx = _legs(inst)
    dates = [d.date for d in inst.days]
    groups = cluster(inst.places, mm, len(dates), inst.cap)
    a = fallback_assignment(inst.places, groups, dates, inst.cap)
    fill_empty_days(a, inst.places, inst.cap)
    move_outdoor_off_rain(a, {p.id: p for p in inst.places}, _weather(inst), inst.cap)
    return repair(inst, to_idx(inst, a), {i: i for i in range(inst.n)}, mm, idx)


# ---- LLM planners ----------------------------------------------------------
def llm_payload(inst: Instance, feedback: list[str] | None = None, previous: dict | None = None) -> str:
    mm, idx = _legs(inst)
    groups = cluster(inst.places, mm, len(inst.days), inst.cap)
    payload = {
        "days": [{"date": d.date.isoformat(), "weather": f"rain chance {round(d.p_wet * 100)}%",
                  "rainy": d.p_wet >= 0.6, "free_hours": round((d.end - d.start) / 60, 1),
                  "drive_budget_min": d.max_drive} for d in inst.days],
        "max_stops": inst.cap, "budget_note": None,
        "candidates": [{"id": p.id, "name": p.name[:60], "kind": p.kind, "outdoor": inst.outdoor[i],
                        "cluster_id": groups.get(p.id, 0), "drive_min": inst.leg(None, i)}
                       for i, p in enumerate(inst.places)],
    }
    if feedback:
        payload["previous_plan"] = previous
        payload["critic_feedback"] = feedback
    return json.dumps(payload, ensure_ascii=False)


MODULO_SUFFIX = ("\nIf previous_plan and critic_feedback are given, the previous plan was checked by "
                 "verifiers and failed; return a corrected full plan that fixes every listed problem.")


async def llm_assign(inst: Instance, feedback=None, previous=None) -> tuple[ItinOut, int]:
    system = prompts.ITINERARY_SYSTEM + (MODULO_SUFFIX if feedback else "")
    out, metric = await complete_json("planner", ItinOut, system, llm_payload(inst, feedback, previous),
                                      max_tokens=900, temperature=0, node="itinerary")
    return out, metric.prompt_tokens + metric.completion_tokens


def raw_ids(inst: Instance, out: ItinOut) -> list[list[str]]:
    by_date = {d.date: d.place_ids for d in out.days}
    return [list(by_date.get(d.date, [])) for d in inst.days]


def order_all(inst: Instance, assign: list[list[int]]) -> list[list[int]]:
    return [best_order(inst, inst.days[d], ids)[0] for d, ids in enumerate(assign)]


def strict_idx(inst: Instance, raw: list[list[str]]) -> list[list[int]]:
    """The LLM's plan as written (known IDs only, duplicates and overfull days kept, so critics see them)."""
    pos = {p.id: i for i, p in enumerate(inst.places)}
    return [[pos[x] for x in day if x in pos] for day in raw]


async def run_llm(inst: Instance) -> dict[str, dict]:
    res: dict[str, dict] = {}
    mm, idx = _legs(inst)
    t0 = time.perf_counter()
    out, tok = await llm_assign(inst)
    raw = raw_ids(inst, out)
    plan = order_all(inst, strict_idx(inst, raw))
    res["llm_raw"] = {"plan": plan, "raw": raw, "tokens": tok, "calls": 1, "s": time.perf_counter() - t0}
    a = validate_assignment(out, inst.places, [d.date for d in inst.days], inst.cap)
    fill_empty_days(a, inst.places, inst.cap)
    move_outdoor_off_rain(a, {p.id: p for p in inst.places}, _weather(inst), inst.cap)
    res["llm_pipeline"] = {"plan": repair(inst, to_idx(inst, a), {i: i for i in range(inst.n)}, mm, idx),
                           "tokens": tok, "calls": 1, "s": time.perf_counter() - t0}
    calls, tokens, cur_raw, cur = 1, tok, raw, plan
    for _ in range(MODULO_ROUNDS):
        fb = critics.feedback(critics.evaluate(inst, cur, raw_ids=cur_raw))
        if not fb:
            break
        prev = {d.date.isoformat(): ids for d, ids in zip(inst.days, cur_raw, strict=True)}
        try:
            out, tok = await llm_assign(inst, fb, prev)
        except Exception as e:  # noqa: BLE001 - keep the last plan
            log.warning("modulo round failed: %s", e)
            break
        calls, tokens = calls + 1, tokens + tok
        cur_raw = raw_ids(inst, out)
        cur = order_all(inst, strict_idx(inst, cur_raw))
    res["llm_modulo"] = {"plan": cur, "raw": cur_raw, "tokens": tokens, "calls": calls,
                         "s": time.perf_counter() - t0}
    return res


# ---- run -----------------------------------------------------------------
def score_row(inst: Instance, name: str, r: dict, backups=None) -> dict:
    ev = critics.evaluate(inst, r["plan"], backups, raw_ids=r.get("raw"))
    u = evaluate(inst, r["plan"], backups)
    drive = sum(simulate(inst, inst.days[d], o).drive for d, o in enumerate(r["plan"]))
    return {"system": name, "critics": {g: {k: v[0] for k, v in c.items()} for g, c in ev.items()},
            "final_pass": critics.passed(ev), **{f"u_{k}": round(v, 3) for k, v in u.items()},
            "stops": sum(len(o) for o in r["plan"]), "drive_min": drive, "llm_calls": r.get("calls", 0),
            "tokens": r.get("tokens", 0), "seconds": round(r.get("s", 0.0), 3),
            "plan": [[inst.places[i].id for i in o] for o in r["plan"]],
            "plan_b": [[inst.places[i].id for i in o] for o in backups] if backups else None,
            **{k: r[k] for k in ("optimal", "iterations", "cuts", "bound") if k in r}}


async def run_case(path: Path, season: str, series: dict, use_llm: bool) -> list[dict]:
    inst, s = load_instance(path, season, series)
    rows = []
    t0 = time.perf_counter()
    rows.append(score_row(inst, "greedy", {"plan": greedy(inst), "s": time.perf_counter() - t0}))
    for name, robust in (("milp", False), ("milp_robust", True)):
        t0 = time.perf_counter()
        if robust:
            res, backups = solve_robust(inst)
        else:
            res, backups = solve(inst, robust=False), None
        r = {"plan": res.days, "s": time.perf_counter() - t0, "optimal": res.optimal, "iterations": res.iterations,
             "cuts": res.cuts, "bound": round(res.bound, 3)}
        rows.append(score_row(inst, name, r, backups))
    if use_llm:
        for attempt in range(LLM_RETRIES):  # free tiers rate-limit (HTTP 429): back off, don't score an outage
            try:
                for name, r in (await run_llm(inst)).items():
                    rows.append(score_row(inst, name, r))
                break
            except Exception as e:  # noqa: BLE001 - after the last retry it is a genuine non-delivery
                log.warning("%s LLM attempt %d failed: %s", path.stem, attempt + 1, e)
                if attempt == LLM_RETRIES - 1:
                    rows += [{"system": n, "delivered": False, "error": str(e)[:200]}
                             for n in ("llm_raw", "llm_pipeline", "llm_modulo")]
                else:
                    await asyncio.sleep(30 * (attempt + 1))
    meta = {"case_id": s["case"]["case_id"], "season": season, "days": len(inst.days), "pool": inst.n,
            "p_wet": [d.p_wet for d in inst.days], "pace": inst.req.pace.value}
    return [{**meta, "delivered": True, **r} if "delivered" not in r else {**meta, **r} for r in rows]


async def main(use_llm: bool, limit: int | None, delay: float) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sp = SNAP.parent / "precip_series.json"
    series = json.loads(sp.read_text()) if sp.exists() else {}
    paths = sorted(SNAP.glob("*.json"))[:limit]
    out = OUT / ("all.jsonl" if use_llm else "solver.jsonl")
    done = set()
    if out.exists():
        done = {(r["case_id"], r["season"]) for r in map(json.loads, out.read_text().splitlines())}
    sem = asyncio.Semaphore(CONCURRENCY if use_llm else 1)

    async def one(p: Path, season: str) -> None:
        async with sem:
            rows = await run_case(p, season, series, use_llm)
            with out.open("a", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, default=str) + "\n")
            log.info("%s %s: %s", p.stem, season, {r["system"]: (r.get("final_pass"), r.get("u_expected"))
                                                    for r in rows})
            if use_llm:
                await asyncio.sleep(delay)

    await asyncio.gather(*(one(p, season) for season in ("nov", "monsoon") for p in paths
                           if (p.stem, season) not in done))




async def resolve() -> None:
    """Re-run the deterministic systems (greedy, milp, milp_robust) on every case in all.jsonl after a
    solver change, keeping the stored LLM rows (their plans do not depend on the solver)."""
    f = OUT / "all.jsonl"
    rows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()]
    sp = SNAP.parent / "precip_series.json"
    series = json.loads(sp.read_text()) if sp.exists() else {}
    keep = [r for r in rows if r["system"].startswith("llm_")]
    for case, season in sorted({(r["case_id"], r["season"]) for r in rows}):
        keep += await run_case(SNAP / f"{case}.json", season, series, use_llm=False)
    f.write_text("".join(json.dumps(r, default=str) + "\n" for r in keep), encoding="utf-8")
    log.info("re-solved %d rows", len(keep))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--provider", choices=["nvidia", "groq", "gemini"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--delay", type=float, default=2.0)
    ap.add_argument("--resolve", action="store_true", help="re-run solver systems, keep LLM rows")
    a = ap.parse_args()
    if a.provider:
        st = get_settings()
        st.llm_primary, st.llm_fallbacks = a.provider, []
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("httpx", "httpcore", "app"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    asyncio.run(resolve() if a.resolve else main(a.llm, a.limit, a.delay))

