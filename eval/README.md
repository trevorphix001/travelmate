# Evaluation: single agent vs multi-agent graph

`cases.jsonl` holds 40 fixed requests. Every origin and destination is in `data/cities.csv`.

| Group | Cases | Why |
|---|---|---|
| North-East India (`ne*`) | 12 | The project's home region and the PRD demo (Guwahati to Shillong) |
| Hill stations (`hs*`) | 10 | Winding roads and outdoor places stress travel time and rain handling |
| Metros and heritage cities (`mt*`) | 10 | Dense OSM data, many candidates, includes two same-city trips |
| Others (`ot*`) | 8 | Coast, pilgrimage and high-altitude trips for variety |

- Lengths are 1 to 5 days, with 1 to 6 travellers, and interests and pace vary.
- 5 cases (`ne11`, `hs10`, `mt10`, `ot07`, `ot08`) have deliberately impossible budgets and carry
  `"expect_infeasible": true`. For these, a correct system answers `within_budget=false` rather than
  pretending to fit.
- Dates are stored as `start_in_days` (20 to 29 days ahead) instead of fixed dates. The cases therefore
  never expire, and they always fall outside the 16-day forecast window, so both systems see the same
  climate-normal weather.
- The cases contain no prices or place names beyond the request fields.

## Running

```
python -m eval.run --system both --limit 3 --provider nvidia   # smoke run
python -m eval.run --system both --provider nvidia             # full run (resumable)
python -m eval.report                                          # writes report.md / report.json
```

Both systems use the same tools and the same LLM provider and model at temperature 0. `--provider`
pins the provider for a fair comparison. The baseline's final answer is scheduled and costed by the
same Python code but is never repaired: invented IDs are counted against its grounding rate and
overfull days against its feasibility. Metric definitions are in `metrics.py`.
