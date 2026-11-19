"""Aggregate experiment rows into paper tables: research/results/report.{json,md}.

    python -m research.report

Tables
  T1  TravelPlanner metrics per system and season (Delivery, Commonsense/Hard Micro/Macro, Final Pass)
  T2  Utility: expected, dry-world and all-wet, as % of the best system on the same case (paired)
  T3  Paired bootstrap 95% CI of milp_robust minus each system (expected utility %, final pass)
  T4  Per-constraint pass rates (which constraints each planner breaks)
  T5  Rain-loss sensitivity (rho) for milp vs milp_robust, monsoon season
  T6  Solver statistics: optimality certificates, Benders iterations, cuts, seconds
"""
import json
import random
from collections import defaultdict
from statistics import mean

from app.planning import critics
from app.planning.robust import evaluate, plan_b
from app.planning.solver import solve
from research.experiment import OUT, SNAP, SYSTEMS, load_instance

SEASONS = ["nov", "monsoon"]
RHOS = [0.3, 0.5, 0.7, 0.9]
B = 2000


def load_rows() -> list[dict]:
    rows: dict[tuple, dict] = {}
    for name in ("solver.jsonl", "all.jsonl"):  # all.jsonl (with LLM) wins for shared systems
        f = OUT / name
        if f.exists():
            for r in map(json.loads, f.read_text(encoding="utf-8").splitlines()):
                rows[(r["case_id"], r["season"], r["system"])] = r
    return rescore(list(rows.values()))


def rescore(rows: list[dict]) -> list[dict]:
    """Re-run the current critic bank on every stored plan, so all rows are judged by the same final rules.
    Within Sandbox needs the planner's raw output, which only the original run saw: it is kept as stored."""
    sp = SNAP.parent / "precip_series.json"
    series = json.loads(sp.read_text()) if sp.exists() else {}
    cache: dict = {}
    for r in rows:
        if "plan" not in r:
            continue
        key = (r["case_id"], r["season"])
        if key not in cache:
            cache[key] = load_instance(SNAP / f"{r['case_id']}.json", r["season"], series)[0]
        inst = cache[key]
        pos = {p.id: i for i, p in enumerate(inst.places)}
        plan = [[pos[x] for x in d if x in pos] for d in r["plan"]]
        backups = [[pos[x] for x in d if x in pos] for d in r["plan_b"]] if r.get("plan_b") else None
        ev = critics.evaluate(inst, plan, backups)
        ev["commonsense"]["Within Sandbox"] = (r["critics"]["commonsense"]["Within Sandbox"], None)
        r["critics"] = {g: {k: v[0] for k, v in c.items()} for g, c in ev.items()}
        r["final_pass"] = critics.passed(ev)
    return rows


def as_eval(r: dict) -> dict | None:
    if not r.get("delivered", True) or "critics" not in r:
        return None
    return {g: {k: (v, None) for k, v in c.items()} for g, c in r["critics"].items()}


def boot_ci(xs: list[float], rng: random.Random) -> tuple[float, float]:
    if not xs:
        return (0.0, 0.0)
    ms = sorted(mean(rng.choices(xs, k=len(xs))) for _ in range(B))
    return ms[int(0.025 * B)], ms[int(0.975 * B)]


def build() -> dict:
    rows = load_rows()
    rng = random.Random(7)
    by = defaultdict(dict)  # (case, season) -> system -> row
    for r in rows:
        by[(r["case_id"], r["season"])][r["system"]] = r
    systems = [s for s in SYSTEMS if any(s in v for v in by.values())]
    # cases where every present system ran (paired comparison)
    llm = [k for k, v in by.items() if all(s in v for s in systems)]
    rep: dict = {"systems": systems, "n_cases": len({k[0] for k in by}), "n_paired": len(llm), "seasons": {}}
    for season in SEASONS:
        keys = [k for k in llm if k[1] == season]
        t1, t2, t4, t3 = {}, {}, {}, {}
        for s in systems:
            evs = [as_eval(by[k][s]) for k in keys]
            t1[s] = {k: round(v, 3) for k, v in critics.pass_rates(evs).items()}
            for metric in ("u_expected", "u_dry", "u_all_wet"):
                rel = []
                for k in keys:
                    best = max(by[k][x].get(metric, 0) for x in systems)
                    rel.append(100 * by[k][s].get(metric, 0) / best if best else 100.0)
                t2.setdefault(s, {})[metric] = round(mean(rel), 1) if rel else None
            t2[s]["llm_calls"] = round(mean(by[k][s].get("llm_calls", 0) for k in keys), 2) if keys else 0
            t2[s]["seconds"] = round(mean(by[k][s].get("seconds", 0) for k in keys), 3) if keys else 0
            t2[s]["drive_min"] = round(mean(by[k][s].get("drive_min", 0) for k in keys), 1) if keys else 0
            per = defaultdict(list)
            for e in evs:
                for g in ("commonsense", "hard"):
                    for name in (critics.COMMONSENSE if g == "commonsense" else critics.HARD):
                        ok = e[g][name][0] if e else False
                        if ok is not None:
                            per[name].append(bool(ok))
            t4[s] = {n: round(mean(v), 3) for n, v in per.items()}
        for s in systems:
            if s == "milp_robust":
                continue
            du, dp = [], []
            for k in keys:
                best = max(by[k][x].get("u_expected", 0) for x in systems) or 1
                du.append(100 * (by[k]["milp_robust"].get("u_expected", 0) - by[k][s].get("u_expected", 0)) / best)
                fp = by[k]["milp_robust"].get("final_pass", False)
                dp.append(float(fp) - float(by[k][s].get("final_pass", False)))
            t3[s] = {"d_expected_pct": round(mean(du), 2) if du else None,
                     "ci_expected": [round(x, 2) for x in boot_ci(du, rng)],
                     "d_final_pass": round(mean(dp), 3) if dp else None,
                     "ci_final_pass": [round(x, 3) for x in boot_ci(dp, rng)]}
        rep["seasons"][season] = {"n": len(keys), "T1": t1, "T2": t2, "T3": t3, "T4": t4,
                                  "p_wet_mean": round(mean(p for k in keys for p in by[k]["milp"]["p_wet"]), 3)
                                  if keys else None}
    solver_rows = [r for r in rows if r["system"] in ("milp", "milp_robust")]
    rep["T6"] = {s: {"optimal_share": round(mean(r.get("optimal", False) for r in rs), 3),
                     "iterations": round(mean(r.get("iterations", 0) for r in rs), 2),
                     "max_iterations": max(r.get("iterations", 0) for r in rs),
                     "cuts": round(mean(r.get("cuts", 0) for r in rs), 2),
                     "seconds": round(mean(r.get("seconds", 0) for r in rs), 3),
                     "max_seconds": round(max(r.get("seconds", 0) for r in rs), 3)}
                 for s in ("milp", "milp_robust")
                 if (rs := [r for r in solver_rows if r["system"] == s])}
    rep["T5"] = sensitivity()
    rep["examples"] = examples(by)
    return rep


def sensitivity() -> dict:
    """Expected utility gain of the robust objective + Plan B over the nominal MILP, for several rain losses."""
    sp = SNAP.parent / "precip_series.json"
    series = json.loads(sp.read_text()) if sp.exists() else {}
    out = {}
    for rho in RHOS:
        gains, worst = [], []
        for p in sorted(SNAP.glob("*.json")):
            inst, _ = load_instance(p, "monsoon", series)
            nom = solve(inst, robust=False).days
            rob = solve(inst, robust=True, rain_loss=rho).days
            b = plan_b(inst, rob)
            en, er = evaluate(inst, nom, rain_loss=rho), evaluate(inst, rob, b, rain_loss=rho)
            if en["expected"] > 0:
                gains.append(100 * (er["expected"] - en["expected"]) / en["expected"])
            if en["all_wet"] > 0:
                worst.append(100 * (er["all_wet"] - en["all_wet"]) / en["all_wet"])
        out[str(rho)] = {"expected_gain_pct": round(mean(gains), 2) if gains else None,
                         "all_wet_gain_pct": round(mean(worst), 2) if worst else None, "n": len(gains)}
    return out


def examples(by: dict) -> list[dict]:
    """Concrete plans per case and season, with what the browser needs to recompute expected utility."""
    sp = SNAP.parent / "precip_series.json"
    series = json.loads(sp.read_text()) if sp.exists() else {}
    out = []
    for (case, season), sysrows in sorted(by.items()):
        if "milp_robust" not in sysrows or "plan" not in sysrows["milp_robust"]:
            continue
        inst, snap = load_instance(SNAP / f"{case}.json", season, series)
        ex = {"case_id": case, "season": season,
              "request": {k: snap["case"][k] for k in ("origin", "destination", "days", "pace", "interests",
                                                       "travellers")},
              "dates": [d.date.isoformat() for d in inst.days], "p_wet": [d.p_wet for d in inst.days],
              "places": {p.id: [p.name, p.kind, inst.util[i], inst.outdoor[i]] for i, p in enumerate(inst.places)},
              "systems": {}}
        for s, r in sysrows.items():
            if "plan" not in r:
                ex["systems"][s] = {"delivered": False}
                continue
            ex["systems"][s] = {"plan": r["plan"], "plan_b": r.get("plan_b") or [], "final_pass": r.get("final_pass"),
                                "failed": [n for g in r["critics"].values() for n, ok in g.items() if ok is False],
                                "llm_calls": r.get("llm_calls", 0), "seconds": r.get("seconds", 0)}
        out.append(ex)
    return out


def to_md(rep: dict) -> str:
    lines = [f"# Results ({rep['n_cases']} cases, {rep['n_paired']} paired case-seasons)\n"]
    for season, t in rep["seasons"].items():
        lines += [f"## Season: {season} (n={t['n']}, mean p_wet={t['p_wet_mean']})\n", "### T1 TravelPlanner metrics\n"]
        cols = ["Delivery Rate", "Commonsense Micro Pass Rate", "Commonsense Macro Pass Rate", "Hard Micro Pass Rate",
                "Hard Macro Pass Rate", "Final Pass Rate"]
        lines += ["| system | " + " | ".join(c.replace(" Pass Rate", "") for c in cols) + " |",
                  "|---" * (len(cols) + 1) + "|"]
        lines += [f"| {s} | " + " | ".join(f"{100 * v[c]:.1f}" for c in cols) + " |" for s, v in t["T1"].items()]
        lines += ["\n### T2 Utility (% of best per case), cost\n",
                  "| system | expected | dry | all-wet | LLM calls | seconds | drive min |",
                  "|---|---|---|---|---|---|---|"]
        lines += [f"| {s} | {v['u_expected']} | {v['u_dry']} | {v['u_all_wet']} | {v['llm_calls']} | {v['seconds']} | "
                  f"{v['drive_min']} |" for s, v in t["T2"].items()]
        lines += ["\n### T3 milp_robust minus system (paired, bootstrap 95% CI)\n",
                  "| vs | expected utility (pp) | 95% CI | final pass | 95% CI |", "|---|---|---|---|---|"]
        lines += [f"| {s} | {v['d_expected_pct']} | {v['ci_expected']} | {v['d_final_pass']} | {v['ci_final_pass']} |"
                  for s, v in t["T3"].items()]
        names = critics.COMMONSENSE + critics.HARD
        lines += ["\n### T4 Per-constraint pass rate (%)\n", "| system | " + " | ".join(names) + " |",
                  "|---" * (len(names) + 1) + "|"]
        lines += [f"| {s} | " + " | ".join(f"{100 * v[n]:.0f}" if n in v else "n/a" for n in names) + " |"
                  for s, v in t["T4"].items()]
        lines.append("")
    lines += ["## T5 Rain-loss sensitivity (monsoon): milp_robust + Plan B vs milp\n",
              "| rho | expected gain % | all-wet gain % | n |", "|---|---|---|---|"]
    lines += [f"| {r} | {v['expected_gain_pct']} | {v['all_wet_gain_pct']} | {v['n']} |" for r, v in rep["T5"].items()]
    lines += ["\n## T6 Solver\n", "| system | optimal | iterations (mean/max) | cuts | seconds (mean/max) |",
              "|---|---|---|---|---|"]
    lines += [f"| {s} | {100 * v['optimal_share']:.0f}% | {v['iterations']}/{v['max_iterations']} | {v['cuts']} | "
              f"{v['seconds']}/{v['max_seconds']} |" for s, v in rep["T6"].items()]
    return "\n".join(lines) + "\n"


def main() -> None:
    rep = build()
    (OUT / "report.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    (OUT / "report.md").write_text(to_md(rep), encoding="utf-8")
    print(to_md(rep))  # noqa: T201 - CLI output


if __name__ == "__main__":
    main()
