"""Find price SOURCES with Tavily search and write DRAFT lines for the team to verify.

The app never calls this. Output goes only to data/cost_drafts.csv, never to the cost table:
a person opens each URL, confirms or fixes the number, and copies the line into data/cost_samples.csv
(then: python -m scripts.cost_research --check-urls --write && python -m scripts.seed).

Prices are pulled from page text by a fixed pattern (Rs / ₹ / INR amounts, "no entry fee" -> 0), with
the surrounding text kept as evidence. No LLM chooses a number.

  python -m scripts.draft_prices --city Shillong                      # stay, meals, local + intercity
  python -m scripts.draft_prices --city Shillong --plan plan.json     # + entry fee per place in a saved plan
Each query uses one Tavily search credit (cached for 7 days).
"""
import argparse
import asyncio
import csv
import json
import logging
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from app.core import cache
from app.core.config import get_settings
from app.tools.http import request_json

log = logging.getLogger("draft_prices")
API = "https://api.tavily.com/search"
DRAFTS = Path("data/cost_drafts.csv")
FIELDS = ["city_tier", "category", "item", "unit", "price_inr", "source_url", "checked_on", "note", "evidence"]
MAX_RESULTS = 5
_AMOUNT = re.compile(r"(?:₹|rs\.?|inr)\s*([0-9][0-9,]{0,7})(?:\s*/-)?|([0-9][0-9,]{0,7})\s*(?:rupees|/-)", re.I)
_FREE = re.compile(r"\b(no entry fee|free entry|entry is free|free of cost|no ticket)\b", re.I)

# Wide sanity ranges (Rs) that only discard impossible draft candidates such as phone numbers or review
# counts. They never become prices: a person still verifies every remaining number on its page.
PLAUSIBLE = {"stay": (200, 100_000), "meal": (10, 10_000), "local_transport": (100, 50_000),
             "entry_fee": (0, 10_000)}

# (category, item, unit, query template). Intercity per-km fares need a human to convert to per 100 km,
# so those drafts only collect sources (price left blank).
QUERIES = [
    ("stay", "budget", "room_night", "budget hotel {city} price per night"),
    ("stay", "mid", "room_night", "mid range hotel {city} price per night"),
    ("meal", "budget", "person_meal", "cheap local restaurant {city} meal price"),
    ("local_transport", "budget", "group_day", "{city} local taxi sightseeing full day fare"),
    ("intercity", "state_bus", "person_100km", "{state} state transport bus fare per km"),
    ("intercity", "private_cab", "vehicle_100km", "{state} taxi tariff per km outstation"),
]


def extract(text: str) -> list[tuple[int, str]]:
    """(price, evidence) candidates found in page text. 'Free' phrases count as price 0."""
    out = []
    for m in _FREE.finditer(text):
        out.append((0, text[max(0, m.start() - 60): m.end() + 60]))
    for m in _AMOUNT.finditer(text):
        value = int((m.group(1) or m.group(2)).replace(",", ""))
        if 0 < value < 1_000_000:
            out.append((value, text[max(0, m.start() - 60): m.end() + 60]))
    return [(p, " ".join(e.split())) for p, e in out]


async def search(query: str) -> dict:
    key = get_settings().tavily_api_key
    if not key or not key.get_secret_value():
        sys.exit("TAVILY_API_KEY is not set in .env")
    body = {"query": query, "max_results": MAX_RESULTS, "search_depth": "basic"}
    return await cache.get_or_fetch(
        "tavily", body,
        lambda: request_json("tavily", "POST", API, json=body,
                             headers={"Authorization": f"Bearer {key.get_secret_value()}"}),
        timedelta(days=7))


def drafts_for(result: dict, tier: int, category: str, item: str, unit: str, query: str,
               want_price: bool = True) -> list[dict]:
    rows = []
    for r in result.get("results", []):
        url, text = r.get("url"), r.get("content") or ""
        lo, hi = PLAUSIBLE.get(category, (0, 1_000_000))
        candidates = [c for c in extract(text) if lo <= c[0] <= hi] if want_price else []
        base = {"city_tier": tier, "category": category, "item": item, "unit": unit, "source_url": url,
                "checked_on": date.today().isoformat(), "note": f"DRAFT from Tavily ({query}) - verify on the page"}
        if not candidates:  # keep the source anyway: a person may find the number on the full page
            rows.append({**base, "price_inr": "", "evidence": " ".join(text.split())[:200]})
        for price, evidence in candidates[:3]:
            rows.append({**base, "price_inr": price, "evidence": evidence[:200]})
    return rows


def load_city(name: str) -> dict:
    with Path("data/cities.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["name"].lower() == name.lower():
                return row
    sys.exit(f"{name} is not in data/cities.csv")


def plan_places(path: Path) -> list[tuple[str, str]]:
    """(place id, name) for every stop in a saved TripPlan JSON."""
    plan = json.loads(path.read_text(encoding="utf-8"))
    names = {p["id"]: p["name"] for p in plan["places"]}
    ids = dict.fromkeys(s["place_id"] for d in plan["days"] for s in d["stops"])
    return [(pid, names[pid]) for pid in ids]


async def run(city_name: str, plan: Path | None) -> list[dict]:
    city = load_city(city_name)
    tier = int(city["tier"])
    rows: list[dict] = []
    for category, item, unit, template in QUERIES:
        q = template.format(city=city["name"], state=city["state"])
        row_tier = 0 if category == "intercity" else tier
        rows += drafts_for(await search(q), row_tier, category, item, unit, q, want_price=category != "intercity")
    for pid, name in plan_places(plan) if plan else []:
        q = f"{name} {city['name']} entry fee"
        rows += drafts_for(await search(q), 0, "entry_fee", pid, "person", q)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True, help="a name from data/cities.csv")
    ap.add_argument("--plan", type=Path, help="saved TripPlan JSON: adds an entry-fee search per stop")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    rows = asyncio.run(run(a.city, a.plan))
    new_file = not DRAFTS.exists()
    with DRAFTS.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(rows)
    priced = sum(1 for r in rows if r["price_inr"] != "")
    log.info("wrote %d draft lines (%d with a candidate price) to %s", len(rows), priced, DRAFTS)
    log.info("Verify each on its page, then copy confirmed lines (without 'evidence', note rewritten) "
             "into data/cost_samples.csv")


if __name__ == "__main__":
    main()
