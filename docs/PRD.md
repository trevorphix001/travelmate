--- page 1 ---
TravelMate AI — Product Requirements 
Document
Nov 18, 2026  ·  @KernelX
1. Verdict on the original proposal
Verdict: approve with changes (7/10 as written, about 9/10 with the changes below).
The idea is feasible for a 5th-semester minor project, the agent split is sensible, and the
single-agent vs multi-agent evaluation is its strongest academic component. However, the
proposal names no data source for prices or attractions, and it relies on an LLM for facts it
cannot know. Both of those are where hallucination would come from.
What is strong
Clear separation of concerns across agents with a shared LangGraph state.
An all-free, open-source stack (OSM, OSRM, Open-Meteo, Supabase free tier).
A measurable research question (budget satisfaction, feasibility, latency) over 30 to
50 fixed requests.
A concrete, local demo case (Guwahati to Shillong, Rs 8,000).
Gaps that must be fixed
# Gap in the proposal Why it matters Required change
1 "No hallucination" is
assumed, not
designed
An LLM will invent
attraction names, timings,
fares and distances
Facts come only from tools;
the LLM only selects, orders
and explains tool results.
Every item carries a source
field, and validators reject
anything without one
2 No source for
attractions
Nominatim geocodes, it
does not list attractions
Add the Overpass API (OSM
tourism=* ,
natural=waterfall ,
historic=* ) plus optional
Wikipedia/Wikivoyage
summaries
TravelMate AI — Product Requirements Document
Page 1 of 15
--- page 2 ---
Bottom line: build it, but as a grounded planner. Tools produce facts, Python does the
arithmetic, and the LLM writes the plan around them. That is also what makes the project
defensible in a viva.
# Gap in the proposal Why it matters Required change
3 No source for prices No free API gives Indian
hotel, cab or meal prices
A versioned, curated cost
table (per city tier, per mode)
in the database; the Budget
agent is deterministic Python,
not an LLM, and every figure is
labelled an estimate
4 LLM-decided
Supervisor routing
Adds tokens and
nondeterminism for a
fixed pipeline
The Supervisor does
structured extraction only; the
graph routes deterministically.
LLM routing is used only for
edit requests
5 Single LLM provider Free NVIDIA credits are
limited and every provider
rate-limits
One provider interface with
NVIDIA Nemotron as primary
and Groq, then Gemini, as
fallbacks
6 Weather beyond the
forecast window
Forecasts cover roughly
16 days
Use the forecast inside the
window; outside it, show
climate normals labelled
"typical, not a forecast"
7 Public OSM servers
used as a backend
Nominatim allows at most
1 request/second with an
identifying User-Agent
Rate limiter, User-Agent
header and a Postgres cache
for every geo call
8 Crowded idea GitHub already has many
LangGraph travel
planners
Differentiate on grounded,
cited output, a deterministic
budget solver, India-first
pricing in Rs, and the
published evaluation harness
TravelMate AI — Product Requirements Document
Page 2 of 15
--- page 3 ---
2. Product overview
TravelMate AI turns a one-line travel request into a cited, budget-checked, map-ready
day-wise itinerary in under 30 seconds, using only free APIs.
Problem. Planning a short Indian trip means juggling maps, blogs, weather sites and
guesswork about costs. Generic chatbots answer fast but invent places, timings and
prices.
Users
Goals (MVP)
1. Parse free text into a validated TripRequest  (origin, destination, dates, travellers,
budget, interests).
2. Return 6 to 15 real attractions per destination, each with OSM coordinates and a
source link.
3. Build a day plan where every stop fits opening hours (when OSM has them) and the
daily time budget.
4. Produce a budget breakdown computed in code, flagged within_budget  or with a
concrete cut suggestion.
5. Show the plan on a Leaflet map with OSRM road routes, plus a daily weather strip.
6. Let users save, list, reload and edit plans ("swap day 2 lunch", "cheaper stay").
7. Ship an evaluation harness over 40 fixed requests that reports the five proposal
metrics.
Non-goals (MVP)
Booking, payments, live hotel or ticket prices, flights.
Multilingual or voice interfaces.
Persona Need Success looks like
Student / budget
traveller
A 2 to 4 day trip under a hard
budget in Rs
Total estimate at or under budget,
with the breakdown visible
Family planner Feasible days, not 9 stops in
one afternoon
Each day fits a time window; travel
time between stops is shown
Evaluator (faculty,
viva panel)
Evidence the multi-agent
design helps
A reproducible benchmark report:
single-agent vs multi-agent
TravelMate AI — Product Requirements Document
Page 3 of 15
--- page 4 ---
Destinations outside India in the curated cost table (other places still plan, but costs
show as "unavailable").
Mobile apps; the web frontend is responsive instead.
3. Grounding and token-efficiency principles
No LLM system can promise zero hallucination; this one makes hallucinated facts unable
to reach the user, because every fact must trace to a tool result or a database row.
Grounding rules (enforced in code, not just in prompts)
1. Tools own facts. Places, coordinates, distances, durations, weather and prices come
only from Overpass, Nominatim, OSRM, Open-Meteo, Wikipedia or the cost table.
2. The LLM selects by ID. Agents receive candidate lists with stable IDs (for example
osm:node/123 ) and must return IDs, never free-typed place names. Pydantic
validators drop any ID not in the candidate set.
3. Python does arithmetic. Budget totals, per-person splits, day durations and distance
sums are computed in code. The LLM never outputs a number that is shown as a fact.
4. Structured output everywhere. Every LLM call returns JSON validated against a
Pydantic schema. On failure: one repair retry with the validation error, then a
deterministic fallback.
5. Provenance on every item. Each attraction, leg and cost line carries source  (API
name + URL or table version) and confidence  ( verified , estimated ,
unavailable ).
6. Honest gaps. Missing data shows as "unavailable", never a guess. Opening hours
absent in OSM display as "hours not listed".
7. Final validator node. Before returning, the Aggregator checks: every ID exists, every
day fits its time window, the budget figure equals the sum of its lines, and the dates
match the request.
Token-efficiency rules
Technique Where Effect
Deterministic graph routing Supervisor Removes one LLM call per step
Compact candidate payloads (id,
name, kind, lat, lon, 1-line note)
Research to
Itinerary
Roughly 40 to 60 tokens per
place instead of raw OSM JSON
TravelMate AI — Product Requirements Document
Page 4 of 15
--- page 5 ---
4. System architecture
A LangGraph StateGraph  runs one fixed pipeline: three nodes call an LLM (Supervisor,
Research ranking, Itinerary), and everything else is deterministic Python around free
APIs.
Technique Where Effect
Small model for extraction, larger
model for planning
Supervisor vs
Itinerary
Cheaper calls where reasoning is
light
Postgres cache keyed by request
hash and API URL
All tools and LLM
calls
Repeat demos and evaluation
reruns cost zero tokens
No chat history in agent prompts;
state fields only
All agents Prompt size stays flat across
turns
max_tokens  caps and JSON mode All LLM calls Bounded output, no prose
padding
Edit requests patch one day, not
the whole plan
Modify flow One small call instead of a full
rerun
Token and latency logged per
node (LangSmith + DB)
Observability Lets the report quote real
numbers
TravelMate AI — Product Requirements Document
Page 5 of 15
--- page 6 ---
Research, Weather and Transport fan out in parallel after geocoding; an over-budget plan
loops back to the Itinerary agent with a cost cap, at most twice, before the Aggregator
validates and saves it.
Agents and nodes
LangGraph workflow · 4 agents, 3 tools, 1 budget loop
Node Uses
LLM?
Input from
state
Writes to state Hard rule
Supervisor Yes
(small,
fast
model)
raw_query request:
TripRequest ,
errors
Missing required field →
return a clarification
question, stop
Geo
resolver
No request origin_geo ,
dest_geo  (lat, lon,
bbox)
No match → error, never
a guessed coordinate
Research
agent
Yes
(ranking
only)
dest_geo ,
interests
candidates:
list[Place]  (ids,
coords, tags, source)
Output IDs must be a
subset of Overpass
results
TravelMate AI — Product Requirements Document
Page 6 of 15
--- page 7 ---
Shared state (abridged)
class TripState(TypedDict, total=False):
    raw_query: str
    request: TripRequest            # validated Pydantic model
    origin_geo: GeoPoint
    dest_geo: GeoPoint
    candidates: list[Place]         # from Overpass, never from the LLM
    weather: list[DayWeather]
    intercity_options: list[TransportOption]
    days: list[DayPlan]             # place IDs + order + times
    budget: BudgetBreakdown
    within_budget: bool
    retries: int
    warnings: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]
    trace: Annotated[list[NodeMetric], operator.add]   # tokens, ms, provider
The single-agent baseline for evaluation uses the same tools in one ReAct-style agent, so
the comparison isolates orchestration, not data access.
Node Uses
LLM?
Input from
state
Writes to state Hard rule
Weather
tool
No dest_geo ,
dates
weather:
list[DayWeather]
Outside forecast window
→
kind="climate_normal"
Transport
and cost
No origin_geo ,
dest_geo ,
travellers
intercity_options ,
cost_table_version
Prices only from the cost
table
Itinerary
agent
Yes
(main
model)
candidates ,
weather ,
days,
budget_cap
days:
list[DayPlan]
(place IDs + order)
Unknown ID or overfull
day → repair retry
Budget
check
No days , costs budget:
BudgetBreakdown ,
within_budget
Totals = sum of lines,
always
Aggregator No whole state plan: TripPlan ,
warnings
Rejects any item without
source
TravelMate AI — Product Requirements Document
Page 7 of 15
--- page 8 ---
5. Technology stack and free-tier limits
Every service below is free for a student project; the binding limits are LLM daily quotas
and the 1 request/second Nominatim rule, so the design caches aggressively and can
switch LLM providers by config.
LLM providers (all OpenAI-compatible, behind one LLMProvider  interface)
Model IDs change often. The first build prompt makes the agent list the models live from
each provider's /models  endpoint, rather than trusting this table.
Core libraries and services
Role Provider Base URL Model (set via env, not hard-
coded)
Known free limits
Primary NVIDIA
build.nvidia.com
https://integrate.api.nvidia.com/v1 nvidia/nemotron-3-super-
120b-a12b  (planning),
nvidia/nemotron-3-nano-
30b-a3b  (extraction)
Free Developer Program
key ( nvapi-  prefix);
reported at about 1,000
starter credits, roughly
one per call. Check your
account
Fallback
1
Groq https://api.groq.com/openai/v1 openai/gpt-oss-120b  or
llama-3.3-70b-versatile
Per model, per
organisation; e.g. gpt-
oss-120b: 30 RPM, 1,000
RPD, 8K TPM, 200K TPD.
The console limits page
is authoritative
Fallback
2
Google Gemini
API
Gemini OpenAI-compatible endpoint Current free Flash or Flash-
Lite model
Free tier exists, but
Google shows the per-
model numbers only in AI
Studio. Free-tier prompts
may be used to improve
Google products
Layer Choice Version / rule
Language Python 3.11 or 3.12 (LangGraph needs 3.10 or
newer)
API FastAPI + Uvicorn, Pydantic v2 Latest stable, pinned in
requirements.txt
Orchestration LangGraph 1.2.x (1.2.16 was released Oct 16,
2026)
Tracing LangSmith (free developer plan)
+ own node_metrics  table
Optional via env flag
TravelMate AI — Product Requirements Document
Page 8 of 15
--- page 9 ---
If the project is ever commercialised, Open-Meteo, Nominatim and OSRM all need paid or
self-hosted instances; that is out of MVP scope.
6. Backend functional requirements
The backend is complete and fully testable through its OpenAPI docs before any frontend
code exists; the frontend consumes only the endpoints below.
API endpoints (prefix /api/v1 )
Layer Choice Version / rule
HTTP httpx  (async) + tenacity
retries
Timeouts on every call
Database and
auth
Supabase Postgres + Supabase
Auth
Free project; Row Level Security on
Geocoding Nominatim public instance At most 1 request/second for the
whole app, custom User-Agent, no
bulk or autocomplete
Attractions Overpass API (OSM) Public instances ask for under
10,000 requests/day and are not a
sustained backend, so cache
results for 7 days
Routing OSRM public demo server Fair use only; cache all route and
table calls; fallback to haversine ×
1.4 road factor, labelled
"approximate"
Weather Open-Meteo Non-commercial: under 10,000
calls/day, 5,000/hour, 600/minute;
CC BY 4.0 attribution required
Frontend HTML5, CSS3, vanilla JS (ES
modules), Leaflet.js
OSM tiles with attribution
Testing pytest , pytest-asyncio ,
respx  for HTTP mocks
Recorded fixtures, so tests never
hit live APIs
Tooling VS Code, Git + GitHub, .venv ,
ruff
No Docker, matching the team's
setup
TravelMate AI — Product Requirements Document
Page 9 of 15
--- page 10 ---
Core schemas (Pydantic v2)
TripRequest : origin, destination (str), start_date , end_date  (date, end ≥ start, at
most 7 days), travellers  (1 to 10), budget_inr  (int > 0), interests  (enum list:
nature, waterfalls, heritage, food, adventure, religious, shopping, nightlife), pace
(relaxed, normal, packed).
Method + path Auth Purpose Response
GET /health No Liveness + provider
and database checks
{status, providers:
{nvidia,groq,gemini}, db}
POST /plans/parse No Free text →
TripRequest  or
clarification question
TripRequest  or
{needs_clarification,
question}
POST /plans Optional Run the full graph TripPlan  (201)
POST /plans/stream Optional Same, as Server-Sent
Events of node
progress
event: node  … event:
done
GET /plans Yes List the user's saved
plans
[{id, title, dates,
total}]
GET /plans/{id} Owner Fetch one plan TripPlan
POST
/plans/{id}/edit
Owner Edit by instruction
("cheaper stay",
"swap day 2 lunch")
Updated TripPlan  + diff
DELETE /plans/{id} Owner Delete 204
GET /places/search?
q=
No Destination
autocomplete from
cached cities table
(never live
Nominatim)
[{name, state, lat,
lon}]
GET /weather?
lat&lon&start&end
No Weather strip [DayWeather]
POST /eval/run Admin
key
Run the benchmark
on N cases, both
systems
EvalReport
TravelMate AI — Product Requirements Document
Page 10 of 15
--- page 11 ---
Place : id  ( osm:node/123 ), name, kind, lat, lon, opening_hours ?, wiki_url ?,
source , confidence .
DayPlan : date, list of Stop  (place_id, arrive, depart, leg_from_prev  {km, minutes,
source}), meals , notes .
BudgetBreakdown : lines of {category, description, unit_cost, qty, total, source};
grand_total , per_person , within_budget , suggestions .
TripPlan : request, days, budget, weather, intercity_options, warnings, sources[] ,
trace  (tokens, ms per node).
Database tables (Supabase Postgres)
Behaviour rules
1. A day holds 9:00 to 20:00 by default ( relaxed  10:00 to 18:00, packed  8:00 to 21:00),
including travel legs and 60-minute meal slots.
2. Stops per day are capped by pace: 3, 4 or 6.
Table Key columns Notes
profiles id (= auth.users.id), display_name Created by trigger on sign-up
plans id, user_id, title, request jsonb,
plan jsonb, created_at
RLS: owner only
plan_edits id, plan_id, instruction, diff jsonb Audit trail for edits
cost_table version, city_tier, category, item,
min_inr, typical_inr, max_inr,
source_note
Seeded from a CSV the team
researches and cites
cities name, state, tier, lat, lon Seeded list for autocomplete
and tier lookup
api_cache key (sha256), provider, response
jsonb, expires_at
For Nominatim, Overpass,
OSRM, Open-Meteo, LLM
node_metrics run_id, node, provider, model,
prompt_tokens,
completion_tokens, ms, ok
Feeds the evaluation
eval_cases ,
eval_results
case_id, system, metrics jsonb 40 fixed cases, two systems
TravelMate AI — Product Requirements Document
Page 11 of 15
--- page 12 ---
3. Intercity cost uses the cost table's per-km rate for the chosen mode (bus, shared taxi,
private cab, train) × OSRM distance.
4. Over budget → the Budget node proposes downgrades in a fixed order: stay tier, then
transport mode, then paid activities; re-runs the Itinerary agent with the new cap, at
most twice; then returns the cheapest plan with within_budget=false  and the gap
in Rs.
5. Every error returns RFC 7807-style JSON {type, title, detail} ; no stack traces
leak to clients.
7. Frontend requirements (built after the backend)
The frontend is a thin, framework-free client: each screen maps to backend endpoints
that already exist and are tested, so no UI work starts before its endpoint passes its tests.
Screen Endpoints used Must show
Home / planner
form
POST /plans/parse , GET
/places/search
Free-text box plus a structured
form (both fill the same
TripRequest ); clarification
question inline when parsing needs
it
Generating view POST /plans/stream Live agent progress (Supervisor →
Research → … → Aggregator) from
SSE events; cancel button
Plan dashboard TripPlan  from above or GET
/plans/{id}
Day tabs with stops, times and
legs; Leaflet map with numbered
markers and route polylines;
weather strip per day; budget table
+ bar of categories; badges:
verified / estimated / unavailable;
Sources panel
Edit panel POST /plans/{id}/edit Instruction box with example chips;
changed items highlighted from
diff
My plans GET /plans , DELETE
/plans/{id}
Saved plan cards; open, delete
TravelMate AI — Product Requirements Document
Page 12 of 15
--- page 13 ---
UI rules
Responsive from 360 px; keyboard-accessible tabs; WCAG AA contrast.
Every number that came from the cost table carries an "estimate" label; every place
links to its OSM or Wikipedia source.
Map attribution for OSM tiles and "Weather data by Open-Meteo.com" are always
visible.
No business logic in JS: totals, times and distances are rendered exactly as the API
returns them.
Print / export to PDF via a print stylesheet (no extra library).
8. Quality targets, evaluation, phases and risks
The MVP is done when the Guwahati to Shillong demo passes every validator, the test
suite runs offline, and the 40-case benchmark report exists.
Non-functional targets
Screen Endpoints used Must show
Sign in Supabase Auth JS (email magic
link)
JWT sent as Authorization:
Bearer  to the API
Evaluation page
(admin)
GET  stored EvalReport Single vs multi-agent table and
charts for the viva
Area Target
Latency p50 under 20 s, p95 under 45 s for a 3-day plan
(warm cache under 8 s)
LLM usage At most 4 LLM calls and about 12K total tokens per
new plan; edits at most 1 call
Grounding 100% of shown places, legs and costs have
source ; 0 IDs outside candidates (validator-
enforced)
Reliability A provider outage falls through to the next
provider within one retry
TravelMate AI — Product Requirements Document
Page 13 of 15
--- page 14 ---
Evaluation protocol (40 cases)
Cases: 40 fixed requests in eval/cases.jsonl  (mix of NE India, metros, hill stations; 1
to 5 days; tight and loose budgets; 5 deliberately impossible budgets).
Systems: (A) single ReAct agent with the same tools, (B) the LangGraph multi-agent
graph. Same LLM model and temperature 0 for both.
Metrics, all computed in code: budget satisfaction (total ≤ budget, or correct
infeasible flag), feasibility (no day over its window, legs present), preference
coverage (share of requested interests with at least one stop), grounding rate (items
with valid source), latency, tokens, node success rate.
Output: eval/report.md  + JSON, and the admin page in the frontend.
Build phases (each phase = one block of prompts in the Prompt Playbook)
1. Phase 0: repository, environment, provider smoke tests.
2. Phase 1: core config, schemas, database, cache.
3. Phase 2: tool clients (Nominatim, Overpass, OSRM, Open-Meteo, Wikipedia, cost
table).
4. Phase 3: LLM provider layer with fallback and metrics.
5. Phase 4: agents and the LangGraph graph.
6. Phase 5: FastAPI endpoints, auth, streaming, edits.
7. Phase 6: evaluation harness and baseline agent.
8. Phase 7: frontend, screen by screen.
9. Phase 8: hardening, deployment (Render or Railway free tier for API, GitHub Pages or
Netlify for frontend), README and report.
Risks
Area Target
Security Secrets only in .env ; RLS on user tables; CORS
allow-list; input length caps; per-IP rate limit on
/plans
Tests Unit tests for every tool and node; one graph test
on recorded fixtures; coverage at least 70% on
app/
TravelMate AI — Product Requirements Document
Page 14 of 15
--- page 15 ---
Sources
NVIDIA Nemotron 3 Super on build.nvidia.com
NVIDIA Nemotron LLM info
Groq free tier limits summary (benchlm, read Sep 2026) and Groq rate limits docs
Gemini API free tier (benchlm)
LangGraph 2026 overview (FutureAGI)
Nominatim usage policy
Overpass API status and usage policy
Open-Meteo terms
Reference projects: MustafaKocamann/AI-Based-Trip-Planner, abh2050 multi-agent
travel agent, nirbar1985/ai-travel-agent
Risk Likelihood Mitigation
Free LLM quota runs out during
evaluation
High Cache LLM responses by prompt hash;
spread runs across providers; run eval
once and store results
Sparse OSM data for small towns Medium Widen the radius in steps (5, 15, 30 km);
show "few mapped attractions" warning
Cost table seen as made up Medium Cite the source of each row (state
tourism rate cards, bus operator fares,
sampled listings) with a date; show
ranges, not single numbers
Public OSRM or Overpass
downtime
Medium Cache + haversine fallback + alternate
Overpass instance in config
Model IDs renamed Medium IDs in .env ; startup check lists available
models
TravelMate AI — Product Requirements Document
Page 15 of 15