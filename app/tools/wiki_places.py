"""Fallback attractions from Wikipedia GeoSearch, used only when Overpass has no live or cached answer.

One call returns geotagged English Wikipedia articles near a point (radius capped at 10 km by the API).
Only articles whose Wikidata short description names a visitor attraction are kept, so universities,
villages and constituencies are dropped. Coordinates, names and links all come from Wikipedia.
"""
import logging
from datetime import UTC, datetime, timedelta

from app.core import cache
from app.schemas.place import Confidence, GeoPoint, Place, Source
from app.tools.http import get_json

log = logging.getLogger(__name__)
API = "https://en.wikipedia.org/w/api.php"
RADIUS_M = 10_000  # GeoSearch maximum
LIMIT = 50

# description keyword -> (kind, interests). Checked in order; the first match wins.
KINDS: list[tuple[tuple[str, ...], str, list[str]]] = [
    (("waterfall",), "waterfall", ["waterfalls", "nature"]),
    (("national park", "wildlife sanctuary", "nature reserve", "bird sanctuary", "tiger reserve"),
     "nature_reserve", ["nature", "adventure"]),
    (("museum",), "museum", ["heritage"]),
    (("fort",), "fort", ["heritage"]),
    (("palace",), "palace", ["heritage"]),
    (("temple", "church", "cathedral", "mosque", "monastery", "gurdwara", "gompa", "shrine", "dargah", "basilica"),
     "place_of_worship", ["religious"]),
    (("monument", "memorial", "tomb", "stupa", "archaeological site", "ruins", "heritage site"), "monument",
     ["heritage"]),
    (("viewpoint", "view point", "canyon", "gorge"), "viewpoint", ["adventure", "nature"]),
    (("peak", "mountain"), "peak", ["nature", "adventure"]),
    (("cave",), "cave_entrance", ["nature", "adventure"]),
    (("lake", "reservoir"), "lake", ["nature"]),
    (("beach",), "beach", ["nature"]),
    (("park", "garden"), "park", ["nature"]),
    (("market", "bazaar"), "marketplace", ["shopping"]),
]


def classify(description: str) -> tuple[str, list[str]] | None:
    text = description.lower()
    for keys, kind, interests in KINDS:
        if any(k in text for k in keys):
            return kind, interests
    return None


def to_place(page: dict, retrieved_at: datetime) -> Place | None:
    coords = (page.get("coordinates") or [None])[0]
    match = classify(page.get("description") or "")
    if not coords or not match:
        return None
    kind, interests = match
    url = page.get("canonicalurl") or page.get("fullurl")
    return Place(
        id=f"wiki:en/{page['pageid']}", name=page["title"], kind=kind, interests=interests,
        lat=float(coords["lat"]), lon=float(coords["lon"]), wiki_url=url, wiki_tag=f"en:{page['title']}",
        source=Source(name=f"Wikipedia: {page['title']}", url=url, retrieved_at=retrieved_at),
        confidence=Confidence.verified,
    )


async def nearby_places(center: GeoPoint, extra_centres: list[GeoPoint] | None = None,
                        min_places: int = 0) -> list[Place]:
    """Attractions within 10 km of center; if fewer than min_places, also around each extra centre in turn."""
    places = await _around(center)
    for c in extra_centres or []:
        if len(places) >= min_places:
            break
        seen = {p.id for p in places}
        places += [p for p in await _around(c) if p.id not in seen]
    return places


async def _around(center: GeoPoint) -> list[Place]:
    params = {
        "action": "query", "format": "json", "formatversion": 2, "generator": "geosearch",
        "ggscoord": f"{center.lat:.5f}|{center.lon:.5f}", "ggsradius": RADIUS_M, "ggslimit": LIMIT,
        "prop": "coordinates|description|info", "colimit": "max", "inprop": "url",
    }

    async def fetch() -> dict:
        return {"data": await get_json("wikipedia", API, params), "retrieved_at": datetime.now(UTC).isoformat()}

    res = await cache.get_or_fetch("wikipedia", {"geosearch": params}, fetch, timedelta(days=7))
    at = datetime.fromisoformat(res["retrieved_at"])
    pages = res["data"].get("query", {}).get("pages", [])
    places = [p for page in pages if (p := to_place(page, at))]
    log.info("wikipedia geosearch near %s -> %d of %d pages are attractions", center.name, len(places), len(pages))
    return places
