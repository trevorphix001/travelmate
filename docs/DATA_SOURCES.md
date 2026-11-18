# Data sources and how we comply

| Source | Used for | Policy / licence | How we comply (code) |
|---|---|---|---|
| OpenStreetMap Nominatim | Geocoding origin and destination | [Usage policy](https://operations.osmfoundation.org/policies/nominatim/): at most 1 req/s, identifying User-Agent, no bulk use | Cities table checked first (`app/tools/geocode.py`); 1.1 s limiter (`app/core/ratelimit.py`); `APP_USER_AGENT` header (`app/tools/http.py`); 30-day cache |
| Overpass API (OSM) | Attractions near the destination | [Overpass usage](https://wiki.openstreetmap.org/wiki/Overpass_API): fair use, under 10,000 req/day | One query per radius (`app/tools/attractions.py`); 7-day cache; 1 s limiter; one try per instance, then the next public instance |
| OSRM demo server | Road distances, durations and route lines | [OSRM API](https://project-osrm.org/docs/v5.24.0/api/), fair use | One table call plus one route call per day (`app/tools/routing.py`); 30-day cache; fallback is haversine × 1.4 at 30 km/h, labelled `estimated` |
| Open-Meteo | Daily forecast and 5-year climate normals | [Terms](https://open-meteo.com/en/terms): non-commercial, CC BY 4.0 attribution | "Weather data by Open-Meteo.com" is on every weather source and in the page footer (`app/tools/weather.py`, `web/index.html`); 3 h / 30 day caches |
| Geoapify Places API (optional, free key) | OpenStreetMap attractions when every public Overpass server is down and there is no cached result | [Geoapify terms](https://www.geoapify.com/term-and-conditions/); OSM data under ODbL with "© OpenStreetMap contributors" attribution | `app/tools/geoapify_places.py`: same OSM IDs and tags as Overpass; 7-day cache; the key never enters cache keys, source links or logs. Plans say "via Geoapify" |
| Wikipedia REST API / Wikidata | Summaries of up to 300 characters, only for places with a wikipedia or wikidata OSM tag | [Wikimedia API etiquette](https://www.mediawiki.org/wiki/API:Etiquette): identifying User-Agent with contact | `app/tools/wiki.py`; 30-day cache. Wikimedia rejects the placeholder User-Agent, so put a real contact in `APP_USER_AGENT` |
| OSM map tiles | Map background | [Tile usage policy](https://operations.osmfoundation.org/policies/tiles/): attribution, light use | Leaflet attribution control plus the footer credit (`web/js/views/plan.js`) |
| `data/cost_table.csv` | Every price shown | Curated by the team | Each row carries `source_note` and `source_url`, and `scripts/seed.py` refuses empty URLs. Every cost line shows its source and an "estimate" label |
| `data/cities.csv` | Autocomplete and city tier | Coordinates from Nominatim (`scripts/build_cities_seed.py`) | Tiers are a team judgement; review them |

## Unverified starter data

`data/cost_table.csv` ships with **preliminary starter estimates**. They are not yet taken
from a cited source. They are labelled `Preliminary estimate`, and their `source_url` points at this
section instead of a web page. A row counts as preliminary while its `source_url` is not a web link
(`CostRow.unverified` in `app/schemas/data.py`). While any are in use, plans show "Prices are
approximate and may differ from current rates" (`app/agents/budget.py`).

**The team must replace each row with a researched figure and a real source URL** (state transport
fare charts, tourism department rate pages, sampled listings). Then re-run `python -m scripts.seed`.

### How to replace them

1. Research prices by hand: state transport fare charts, tourism department rate pages, ASI's monument
   fee list, and 5–10 sampled hotel or menu listings per tier. Record **every observed price as one
   line** in `data/cost_samples.csv`:

   ```
   city_tier,category,item,unit,price_inr,source_url,checked_on,note
   2,stay,budget,room_night,1300,https://…listing-1,2026-11-20,guesthouse near Police Bazar
   2,stay,budget,room_night,1800,https://…listing-2,2026-11-20,hotel, Laitumkhrah
   0,entry_fee,osm:node/4571342649,person,50,https://…official-fee-page,2026-11-20,Elephant Falls
   ```
   **Optional search aid (Tavily):** `python -m scripts.draft_prices --city Shillong --plan plan.json`
   finds candidate pages and writes **DRAFT** lines, each with its source URL and the text it came
   from, to `data/cost_drafts.csv`. That file is never loaded by the app or the seed. Open each URL,
   confirm or correct the price, and copy only verified lines into `cost_samples.csv` (drop the
   `evidence` column and rewrite the note). Sources often disagree, which is why a person checks.
   Each query costs one Tavily credit; results are cached for 7 days.
2. Preview, then check that every link loads, then write:
   `python -m scripts.cost_research`, then `python -m scripts.cost_research --check-urls --write`.
   Each group of samples becomes one row: min = lowest, typical = median, max = highest. The row is
   cited to its first sample's URL and replaces the matching preliminary row; all other rows are kept.
3. Load the result: `python -m scripts.seed`.

**Place-specific entry fees:** set `item` to the place's ID as shown in a plan (`osm:node/…`, or
`wiki:en/…` for Wikipedia-sourced places), with `city_tier` 0. That row beats the per-kind row, which
beats `default`. The same attraction can have both an OSM and a Wikipedia ID, so add a row for each.

Row conventions (`app/tools/costs.py`):

| category | item | unit |
|---|---|---|
| stay | budget, mid, premium | room_night |
| meal | budget, mid | person_meal |
| intercity | state_bus, shared_taxi, private_cab (add train_sleeper only for routes that have a train) | person_100km or vehicle_100km |
| duration_factor | the same modes | percent_of_car_time (not money; it scales OSRM car time) |
| local_transport | budget, mid | group_day |
| entry_fee | an OSM kind (museum, fort, waterfall, …) or `default` | person |

`city_tier` 0 means the row applies to every tier.
