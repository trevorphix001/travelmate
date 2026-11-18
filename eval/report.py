"""Aggregate eval/results/*.jsonl into eval/report.md and eval/report.json.

Means, medians and 95% bootstrap CIs (1,000 resamples, seed 42) per system, plus a paired
comparison (graph minus baseline) over the cases both systems ran. Run: python -m eval.report
"""
import json
import random
from pathlib import Path
from statistics import mean, median

RESULTS = Path("eval/results")
METRICS = ["success", "budget_ok", "feasible", "preference_coverage", "grounding_rate", "latency_s",
           "total_tokens", "llm_calls", "node_success_rate"]
NOTES = {
    "success": "Run finished without error and produced a plan",
    "budget_ok": "Total <= budget, or within_budget=false for the 5 impossible budgets",
    "feasible": "No day past its time window, every stop has a leg, no repeated place",
    "preference_coverage": "Share of requested interests with at least one matching stop",
    "grounding_rate": "Share of stops/legs/cost lines with a valid source (invented IDs count as failures)",
    "latency_s": "Wall-clock seconds per request",
    "total_tokens": "Prompt + completion tokens per request",
    "llm_calls": "LLM calls per request",
    "node_success_rate": "Share of graph nodes / agent steps that completed",
}
B, SEED = 1000, 42


def load(system: str) -> dict[str, dict]:
    f = RESULTS / f"{system}.jsonl"
    if not f.exists():
        return {}
    rows = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {r["case_id"]: r for r in rows}


def bootstrap_ci(xs: list[float], rng: random.Random) -> tuple[float, float]:
    if len(xs) < 2:
        return (xs[0], xs[0]) if xs else (0.0, 0.0)
    means = sorted(mean(rng.choices(xs, k=len(xs))) for _ in range(B))
    return means[int(0.025 * B)], means[int(0.975 * B) - 1]


def summarize(rows: dict[str, dict]) -> dict:
    rng = random.Random(SEED)
    out = {}
    for m in METRICS:
        xs = [float(r[m]) for r in rows.values() if m in r and r[m] is not None]
        if xs:
            lo, hi = bootstrap_ci(xs, rng)
            out[m] = {"n": len(xs), "mean": round(mean(xs), 4), "median": round(median(xs), 4),
                      "ci95": [round(lo, 4), round(hi, 4)]}
    return out


def paired(a: dict[str, dict], b: dict[str, dict]) -> dict:
    rng = random.Random(SEED)
    common = sorted(set(a) & set(b))
    out = {}
    for m in METRICS:
        diffs = [float(a[c][m]) - float(b[c][m]) for c in common if m in a[c] and m in b[c]]
        if diffs:
            lo, hi = bootstrap_ci(diffs, rng)
            out[m] = {"n": len(diffs), "mean_diff": round(mean(diffs), 4), "ci95": [round(lo, 4), round(hi, 4)]}
    return out


def build() -> dict:
    g, b = load("graph"), load("baseline")
    return {"systems": {"graph": summarize(g), "baseline": summarize(b)}, "paired_graph_minus_baseline": paired(g, b),
            "notes": NOTES, "cases": {"graph": len(g), "baseline": len(b)}}


def to_markdown(rep: dict) -> str:
    lines = ["# TravelMate AI evaluation report", "",
             f"Cases run: multi-agent graph = {rep['cases']['graph']}, single-agent baseline = "
             f"{rep['cases']['baseline']}. 95% CIs from {B} bootstrap resamples (seed {SEED}).", "",
             "| Metric | Graph mean [95% CI] | Graph median | Baseline mean [95% CI] | Baseline median | "
             "Paired diff (graph-baseline) [95% CI] |", "|---|---|---|---|---|---|"]
    for m in METRICS:
        g = rep["systems"]["graph"].get(m)
        b = rep["systems"]["baseline"].get(m)
        p = rep["paired_graph_minus_baseline"].get(m)

        def fmt(x):
            return f"{x['mean']} [{x['ci95'][0]}, {x['ci95'][1]}]" if x else "n/a"

        lines.append(f"| {m} | {fmt(g)} | {g['median'] if g else 'n/a'} | {fmt(b)} | {b['median'] if b else 'n/a'} | "
                     + (f"{p['mean_diff']} [{p['ci95'][0]}, {p['ci95'][1]}] |" if p else "n/a |"))
    lines += ["", "## How each metric is computed", ""] + [f"- **{k}**: {v}" for k, v in NOTES.items()]
    return "\n".join(lines) + "\n"


def main() -> None:
    rep = build()
    Path("eval/report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    Path("eval/report.md").write_text(to_markdown(rep), encoding="utf-8")
    web = Path("web/data")
    web.mkdir(parents=True, exist_ok=True)
    (web / "report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
