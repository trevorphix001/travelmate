# TravelMate: A Multi-Agent LLM System for Budget-Aware, Day-by-Day Trip Planning

TravelMate turns a one-line request ("3 days from Guwahati to Shillong for 2, Rs 8000, waterfalls")
into a cited, budget-checked, map-ready day-wise itinerary, using only free APIs.

**The design is grounded.** Tools produce the facts: OpenStreetMap, OSRM, Open-Meteo, Wikipedia and
the curated cost table. Python does all the arithmetic. The LLM (NVIDIA Nemotron, with Groq and
Gemini as fallbacks) only extracts the request, ranks candidate IDs and groups IDs into days. Any ID
the LLM invents is dropped, and every item shown carries a `source` and a `confidence`
(`verified` / `estimated` / `unavailable`).

- Spec: [docs/PRD.md](docs/PRD.md) · **architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**
- Graph: [docs/graph.md](docs/graph.md) · sources and compliance: [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) · [docs/LIMITATIONS.md](docs/LIMITATIONS.md)

## Research extension: TravelMate-R

Three features, each building on a recent paper. Code is in `app/planning/`, experiments in `research/`.

| Feature | What it does | Builds on |
|---|---|---|
| Solver-grounded day planning | Logic-based Benders: a MILP (HiGHS) assigns places to days, an exact minute-by-minute day verifier adds no-good cuts, and the loop ends with an optimality certificate. No LLM call in day planning. | Hao et al. 2024 (SMT planning); TTG (Ju et al. 2024); Hooker & Ottosson 2003 |
| Critic bank | TravelPlanner's constraint evaluator ported to day trips (10 constraints) and its metrics (Delivery, Commonsense/Hard Micro/Macro, Final Pass). The same critics back-prompt the LLM-Modulo baseline. | Xie et al. ICML 2024 (code: OSU-NLP-Group/TravelPlanner, MIT); Kambhampati et al. ICML 2024; Gundawar et al. 2024 |
| Weather-robust plans | Wet-day probability per day (forecast, or 10-year climatology with the IMD 2.5 mm rule). The solver maximises expected utility and reserves an indoor Plan B for each likely-wet outdoor day. The UI shows the rain chance and the Plan B. | New: existing benchmarks assume the weather is known |

`PLANNER=robust` (default) uses the solver; `PLANNER=llm` restores the LLM day grouping.
`STATELESS=true` runs with no database at all: reference data comes from `data/*.csv`, caches stay in
memory, and saved trips live in the browser (`web/js/store.js`). Edits go through the stateless
`POST /api/v1/plans/edit`.

```powershell
python -m research.snapshot                                # freeze tool outputs for the 40 eval cases
python -m research.experiment                              # greedy + solver systems (no LLM)
python -m research.experiment --llm --provider nvidia      # adds the three LLM systems
python -m research.report                                  # research/results/report.{md,json}
python -m research.build_demo                              # research/demo/index.html (self-contained)
```

Paper draft: [research/PAPER.md](research/PAPER.md) · results: [research/results/report.md](research/results/report.md)

## Architecture

![TravelMate system architecture](docs/architecture.png)

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Pipeline at a glance:

```
Supervisor (LLM, extract) ─► Geo ─┬─► Research (Overpass + LLM rank by ID) ─┐
                                  ├─► Weather (Open-Meteo)                   ├─► Itinerary (LLM groups IDs; Python schedules)
                                  └─► Transport (OSRM + cost table)          ┘        │
                                                         ┌──── over budget, ≤2× ────┤
                                                         ▼                          ▼
                                                    Itinerary ◄── Budget (Python) ──► Aggregator (validators) ─► TripPlan
```

| Path | What lives there |
|---|---|
| `app/tools/` | One client per free data source, all cached (`app/core/cache.py`) and rate-limited |
| `app/llm/` | `complete_json()`: provider fallback, JSON validation, a repair retry, caching and metrics; prompts |
| `app/agents/` | Graph nodes (supervisor, geo, research, weather, transport, itinerary, budget, aggregator) plus the plan editor |
| `app/graph/build.py` | LangGraph `StateGraph` wiring |
| `app/api/` | FastAPI endpoints under `/api/v1`, Supabase JWT auth, rate limits |
| `app/baseline/agent.py` | Single-agent ReAct baseline for the evaluation |
| `eval/` | 40 cases, metrics, runner and bootstrap report |
| `web/` | Framework-free frontend (ES modules and Leaflet) |

At most 4 LLM calls are made per new plan (Supervisor, Research, Itinerary, plus one more Itinerary
run if the budget loop cuts paid activities), and edits make 1 call.

## Setup (Windows PowerShell; Linux/macOS differences in comments)

```powershell
py -3 -m venv .venv                      # Linux: python3 -m venv .venv
.venv\Scripts\Activate.ps1               # Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                   # Linux: cp .env.example .env
```

Fill in `.env`:

| Variable | Where to get it |
|---|---|
| `NVIDIA_API_KEY` | build.nvidia.com → API keys (`nvapi-…`) |
| `GROQ_API_KEY`, `GEMINI_API_KEY` | console.groq.com, aistudio.google.com (optional fallbacks) |
| `MODEL_*` | Run `python -m scripts.smoke_providers` and copy IDs **from its output** |
| `DATABASE_URL` | Supabase → Project Settings → Database → connection string (session pooler) |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | Supabase → Project Settings → API (also put them in `web/js/config.js`) |
| `SUPABASE_JWT_SECRET` | Legacy HS256 projects only; newer projects are verified through JWKS automatically |
| `APP_USER_AGENT` | `TravelMateAI/0.1 (contact: you@example.com)`: required by Nominatim and Wikimedia |
| `ADMIN_KEY` | Any long random string; protects `POST /api/v1/eval/run` |

Create the tables and load the reference data:

```powershell
python -m scripts.migrate                # or paste db/migrations/001_init.sql into the Supabase SQL editor
python -m scripts.seed                   # loads data/cost_table.csv and data/cities.csv
```

## Run

```powershell
uvicorn app.main:app --reload --port 8000         # API docs at http://127.0.0.1:8000/docs
$env:STATELESS="true"; uvicorn app.main:app --port 8000   # no database: trips are saved in the browser
python -m http.server 5500 --directory web        # frontend at http://127.0.0.1:5500
python -m scripts.run_demo                        # live golden run of the PRD demo request
```

`http://127.0.0.1:5500/#/plan/fixture` renders `web/data/golden.json` without calling the API.

## Tests

```powershell
pytest -q                 # offline; recorded fixtures in tests/fixtures, no network
ruff check .
pytest -m live tests/test_live_record.py -s   # re-record fixtures from the real APIs (rarely)
```

## Evaluation

```powershell
python -m eval.run --system both --limit 3 --provider nvidia   # smoke run
python -m eval.run --system both --provider nvidia             # full 40 cases (resumable)
python -m eval.report                                          # eval/report.md + web/data/report.json
```

See [eval/README.md](eval/README.md) for how the cases were chosen and what each metric means.

## Deploy (Render)

`render.yaml` describes two free Render services:

- **travelmate-api**: the FastAPI backend. Render asks for the secret values (copy them from your `.env`;
  use the Supabase *Session pooler* URL for `DATABASE_URL`, because Render has no IPv6).
- **travelmate-web**: the `web/` folder as a static site.

In Render choose **New → Blueprint** and pick this repository. The website calls `http://127.0.0.1:8000`
when opened locally and `https://travelmate-api.onrender.com` otherwise (`web/js/config.js`). Add the
website address to Supabase *Authentication → URL Configuration* so sign-in links work.

The free API sleeps when idle, so the first request after a while can take up to a minute.

## Status

The backend, LangGraph pipeline, evaluation harness, frontend and the TravelMate-R research extension
are implemented, with 167 offline tests and a clean ruff run. The research benchmark covers all 40 cases
in two weather regimes on frozen tool outputs (`research/results/report.md`). The live graph-vs-baseline
run in `eval/` is still to do. The cost table
holds preliminary starter prices that the team must replace with researched, cited figures
(see [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)).
