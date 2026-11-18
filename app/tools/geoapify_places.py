"""OpenStreetMap attractions via the Geoapify Places API: the first fallback when public Overpass is down.

Geoapify serves OSM data from its own servers and returns each feature's original OSM id and tags
(properties.datasource.raw). We rebuild an Overpass-style element from those and reuse
attractions.to_place, so ids (osm:node/123), kinds, interests, hours, fees and wiki links match Overpass.
Geoapify has no waterfall category; waterfalls arrive through tourism.attraction plus the name hints.
"""
import logging
from datetime import UTC, datetime, timedelta

import httpx

from app.core import cache
from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.place import GeoPoint, Place
from app.schemas.trip import Interest
from app.tools.attractions import MIN_PLACES, RADII_KM, dedupe, relevant_count, to_place
from app.tools.http import get_json

log = logging.getLogger(__name__)
API = "https://api.geoapify.com/v2/places"
LIMIT = 500  # API maximum per request

# Category names checked against the live API (invalid ones return HTTP 400). A parent category is never
# mixed with its own children in one request: that combination returned almost nothing.
ALWAYS = ["tourism.attraction", "tourism.sights", "entertainment.museum"]
CATEGORIES: dict[Interest, list[str]] = {
    Interest.nature: ["natural.mountain.peak", "natural.mountain.cave_entrance", "natural.protected_area",
                      "national_park", "leisure.park"],
    Interest.waterfalls: [],  # no such category: tourism.attraction + name hints cover them
    Interest.heritage: [],  # tourism.sights and entertainment.museum are always requested
    Interest.religious: ["religion.place_of_worship"],
    Interest.food: ["catering.restaurant", "catering.cafe"],
    Interest.adventure: ["natural.mountain.peak", "activity", "sport"],
    Interest.shopping: ["commercial.shopping_mall", "commercial.marketplace"],
    Interest.nightlife: ["catering.bar", "catering.pub", "adult.nightclub"],
}
_OSM_TYPES = {"n": "node", "w": "way", "r": "relation", "node": "node", "way": "way", "relation": "relation"}


def categories_for(interests: list[Interest]) -> str:
    cats = list(ALWAYS)
    for i in sorted(interests):
        cats += CATEGORIES.get(i, [])
    return ",".join(dict.fromkeys(cats))


def to_element(feature: dict) -> dict | None:
    """Geoapify feature -> Overpass-style element {type, id, lat, lon, tags} using the raw OSM data."""
    props = feature.get("properties") or {}
    raw = (props.get("datasource") or {}).get("raw") or {}
    osm_type = _OSM_TYPES.get(str(raw.get("osm_type", "")))
    if (props.get("datasource") or {}).get("sourcename") != "openstreetmap" or not osm_type or not raw.get("osm_id"):
        return None
    tags = {k: str(v) for k, v in raw.items() if k not in {"osm_id", "osm_type", "lat", "lon"}}
    return {"type": osm_type, "id": int(raw["osm_id"]), "lat": props.get("lat"), "lon": props.get("lon"), "tags": tags}


def keep(tags: dict) -> bool:
    """Same rule as the Overpass query: a place of worship counts only if tagged for tourists or notable."""
    is_worship = tags.get("amenity") == "place_of_worship" or "religion" in tags
    return not is_worship or any(k in tags for k in ("tourism", "wikidata", "wikipedia"))


def parse(data: dict, retrieved_at: datetime) -> list[Place]:
    places = []
    for f in data.get("features", []):
        el = to_element(f)
        if el and keep(el["tags"]) and (p := to_place(el, retrieved_at)):
            src = p.source.model_copy(update={"name": "OpenStreetMap (via Geoapify)"})
            places.append(p.model_copy(update={"source": src}))
    return dedupe(places)


async def _fetch(center: GeoPoint, radius_km: int, categories: str) -> dict:
    key = get_settings().geoapify_api_key
    params = {"categories": categories, "filter": f"circle:{center.lon:.5f},{center.lat:.5f},{radius_km * 1000}",
              "bias": f"proximity:{center.lon:.5f},{center.lat:.5f}", "limit": LIMIT}

    async def fetch() -> dict:
        try:
            data = await get_json("geoapify", API, {**params, "apiKey": key.get_secret_value()})
        except Exception as e:  # noqa: BLE001 - the request URL carries the key: never let any error text through
            detail = f"HTTP {e.response.status_code}" if isinstance(e, httpx.HTTPStatusError) else type(e).__name__
            raise AppError("geoapify_unavailable", "Geoapify places request failed", detail, 503) from None
        return {"data": data, "retrieved_at": datetime.now(UTC).isoformat()}

    return await cache.get_or_fetch("geoapify", params, fetch, timedelta(days=7))  # params exclude the key


def enabled() -> bool:
    key = get_settings().geoapify_api_key
    return bool(key and key.get_secret_value())


async def find_places(center: GeoPoint, interests: list[Interest], min_places: int = MIN_PLACES) -> list[Place]:
    """Widen 5 -> 15 -> 30 -> 60 km until min_places places match the requested interests."""
    cats = categories_for(interests)
    places: list[Place] = []
    for r in RADII_KM:
        res = await _fetch(center, r, cats)
        places = parse(res["data"], datetime.fromisoformat(res["retrieved_at"]))
        found = relevant_count(places, interests)
        log.info("geoapify radius=%dkm -> %d places, %d relevant (need %d)", r, len(places), found, min_places)
        if found >= min_places:
            break
    return places
