"""Build data/cities.csv by geocoding a hand-picked list of destinations with Nominatim (1 req/s).

Coordinates and states come from OpenStreetMap; the tier (1 metro, 2 city/major hill station, 3 town)
is the team's judgement and should be reviewed. Run: python -m scripts.build_cities_seed
"""
import asyncio
import csv
import logging
from pathlib import Path

from app.core.config import get_settings
from app.core.ratelimit import limiter
from app.tools.geocode import NOMINATIM
from app.tools.http import get_json

log = logging.getLogger("cities")
OUT = Path("data/cities.csv")

# (display name, query, tier)
CITIES: list[tuple[str, str, int]] = [
    # North-East India
    ("Guwahati", "Guwahati", 2), ("Shillong", "Shillong", 2), ("Sohra", "Sohra, Meghalaya", 3),
    ("Dawki", "Dawki, Meghalaya", 3), ("Mawlynnong", "Mawlynnong", 3), ("Tawang", "Tawang", 3),
    ("Gangtok", "Gangtok", 2), ("Pelling", "Pelling", 3), ("Darjeeling", "Darjeeling", 2),
    ("Kohima", "Kohima", 3), ("Imphal", "Imphal", 3), ("Aizawl", "Aizawl", 3), ("Agartala", "Agartala", 3),
    ("Itanagar", "Itanagar", 3), ("Tezpur", "Tezpur", 3), ("Jorhat", "Jorhat", 3), ("Ziro", "Ziro", 3),
    ("Dibrugarh", "Dibrugarh", 3), ("Kaziranga", "Kohora, Assam", 3), ("Majuli", "Garamur, Majuli", 3),
    # Hill stations
    ("Shimla", "Shimla", 2), ("Manali", "Manali, Himachal Pradesh", 2), ("Mussoorie", "Mussoorie", 2),
    ("Nainital", "Nainital", 2), ("Ooty", "Ooty", 2), ("Munnar", "Munnar", 2), ("Kodaikanal", "Kodaikanal", 2),
    ("Mount Abu", "Mount Abu", 3), ("Dharamshala", "Dharamshala", 2), ("Madikeri", "Madikeri", 3),
    ("Lonavala", "Lonavala", 3), ("Mahabaleshwar", "Mahabaleshwar", 3),
    # Metros and heritage cities
    ("Delhi", "New Delhi", 1), ("Mumbai", "Mumbai", 1), ("Kolkata", "Kolkata", 1), ("Chennai", "Chennai", 1),
    ("Bengaluru", "Bengaluru", 1), ("Hyderabad", "Hyderabad, Telangana", 1), ("Agra", "Agra", 2),
    ("Jaipur", "Jaipur", 2), ("Udaipur", "Udaipur, Rajasthan", 2), ("Jodhpur", "Jodhpur", 2),
    ("Varanasi", "Varanasi", 2), ("Hampi", "Hampi", 3), ("Mysuru", "Mysuru", 2), ("Khajuraho", "Khajuraho", 3),
    ("Amritsar", "Amritsar", 2),
    # Others
    ("Panaji", "Panaji", 2), ("Rishikesh", "Rishikesh", 2), ("Haridwar", "Haridwar", 2),
    ("Puducherry", "Puducherry", 2), ("Kochi", "Kochi", 2), ("Alappuzha", "Alappuzha", 3), ("Leh", "Leh", 3),
    ("Srinagar", "Srinagar", 2), ("Puri", "Puri", 3), ("Bhubaneswar", "Bhubaneswar", 2),
]


async def main() -> None:
    log.info("User-Agent: %s", get_settings().app_user_agent)
    rows = []
    for name, query, tier in CITIES:
        await limiter("nominatim").wait()
        res = await get_json("nominatim", NOMINATIM, {"q": query, "format": "jsonv2", "limit": 1,
                                                      "countrycodes": "in", "addressdetails": 1})
        if not res:
            log.warning("no result for %s", query)
            continue
        top = res[0]
        addr = top.get("address", {})
        state = addr.get("state") or addr.get("union_territory") or addr.get("territory") or ""
        rows.append({"name": name, "state": state, "tier": tier,
                     "lat": round(float(top["lat"]), 5), "lon": round(float(top["lon"]), 5)})
        log.info("%-14s %-22s %s,%s", name, state, rows[-1]["lat"], rows[-1]["lon"])
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["name", "state", "tier", "lat", "lon"])
        w.writeheader()
        w.writerows(rows)
    log.info("wrote %d rows to %s", len(rows), OUT)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(main())
