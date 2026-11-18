# Known limitations

- **Prices are estimates.** The cost table is a curated range, never live hotel, ticket or cab
  prices. The shipped starter rows are unverified (see `docs/DATA_SOURCES.md`). Train fares are
  omitted because availability depends on the route.
- **OSM coverage gaps.** Small towns may have few named attractions. Research then warns "Few mapped
  attractions" (`app/agents/research.py`). Many POIs carry only `tourism=attraction`, so interests
  are partly inferred from English name keywords (`NAME_HINTS` in `app/tools/attractions.py`).
- **Search radius.** Attractions are searched at 5, 15, 30 and then 60 km until there are enough
  places matching the requested interests for the trip length (days × stops per day + 3). Far
  places become day trips with long legs. The Wikipedia fallback covers 10 km around the destination
  plus 10 km around each curated city within 60 km.
- **Opening hours.** Only simple `HH:MM-HH:MM` patterns and `24/7` are parsed. Weekday rules are
  ignored (`opening_window` in `app/agents/itinerary.py`). Anything else shows "hours not listed".
- **Realism rules.** OSRM's public car profile assumes free-flowing roads (62–81 km/h for
  Guwahati–Shillong), so leg times are capped at 25 km/h for legs up to 15 km and 40 km/h beyond
  (`realistic_minutes` in `app/tools/routing.py`). Capped legs are labelled `estimated`. Each day also
  has a total road-time budget (relaxed 3 h, normal 4 h, packed 5 h) that includes the intercity trip
  on arrival and departure days. Over budget, the stop farthest from the stay is dropped and noted
  (`schedule_day` in `app/agents/itinerary.py`). These are planning assumptions, not measurements.
- **Travel times.** OSRM's car profile is optimistic on Indian hill roads. The straight-line
  fallback uses 30 km/h everywhere because the app has no elevation data (`app/tools/routing.py`).
- **Weather.** Forecasts cover about 16 days. Later dates show a 5-year climate normal labelled
  "typical, not a forecast".
- **Public free services.** Nominatim, Overpass, OSRM and Open-Meteo are shared public servers.
  Overpass in particular can be overloaded: when every configured instance fails, the plan fails
  with a clear "service not responding" error within about a minute. Results are cached in
  `api_cache` so repeat requests and evaluation reruns don't depend on them.
- **Free LLM quotas.** NVIDIA, Groq and Gemini free tiers rate-limit. The provider layer falls
  through to the next provider and caches validated outputs for 7 days. Research and Itinerary have
  deterministic fallbacks, so a plan still completes (with a warning) when every LLM is down. The
  Supervisor's free-text parsing does not; use the structured form instead.
- **Dates.** "Next Friday" is resolved by the LLM against today's date and then validated in Python.
  An ambiguous phrase can still resolve to an unexpected but valid date, so the planner shows the
  parsed form for confirmation before generating.
- **Rate limiting** is in-memory per process; a multi-instance deployment needs a shared store.
- **Body size cap** relies on `Content-Length`; chunked uploads without it are not capped.
