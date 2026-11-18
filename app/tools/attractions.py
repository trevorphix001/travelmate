"""Attractions from the Overpass API (OSM). Only elements with a name tag; cached 7 days."""
import logging
from datetime import UTC, datetime, timedelta

from app.core import cache
from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.place import Confidence, GeoPoint, Place, Source
from app.schemas.trip import Interest
from app.tools.geo import haversine_km
from app.tools.http import request_once

log = logging.getLogger(__name__)
RADII_KM = (5, 15, 30, 60)  # 60 km reaches day-trip areas for multi-day trips (e.g. Sohra from Shillong)
MIN_PLACES = 8

# Overpass filter clauses per interest (tag names checked against the OSM wiki Map Features pages).
ALWAYS = ['["tourism"~"^(attraction|viewpoint)$"]']
FILTERS: dict[Interest, list[str]] = {
    Interest.nature: ['["natural"~"^(peak|waterfall|cave_entrance)$"]', '["leisure"~"^(park|nature_reserve)$"]'],
    Interest.waterfalls: ['["natural"="waterfall"]'],
    Interest.heritage: ['["historic"]', '["tourism"="museum"]'],
    Interest.religious: ['["amenity"="place_of_worship"]["tourism"]', '["amenity"="place_of_worship"]["wikidata"]'],
    Interest.food: ['["amenity"~"^(restaurant|cafe)$"]["cuisine"]'],
    Interest.adventure: ['["tourism"="viewpoint"]', '["sport"]'],
    Interest.shopping: ['["shop"="mall"]', '["amenity"="marketplace"]'],
    Interest.nightlife: ['["amenity"~"^(bar|pub|nightclub)$"]'],
}

# Python-side predicates mirroring FILTERS, used to label which interests a place matched.
MATCHERS: dict[str, callable] = {
    "nature": lambda t: t.get("natural") in {"peak", "waterfall", "cave_entrance"}
    or t.get("leisure") in {"park", "nature_reserve"},
    "waterfalls": lambda t: t.get("natural") == "waterfall",
    "heritage": lambda t: "historic" in t or t.get("tourism") == "museum",
    "religious": lambda t: t.get("amenity") == "place_of_worship",
    "food": lambda t: t.get("amenity") in {"restaurant", "cafe"},
    "adventure": lambda t: t.get("tourism") == "viewpoint" or "sport" in t,
    "shopping": lambda t: t.get("shop") == "mall" or t.get("amenity") == "marketplace",
    "nightlife": lambda t: t.get("amenity") in {"bar", "pub", "nightclub"},
}

# Order decides which tag names the place's kind when several match.
KIND_KEYS = ("natural", "tourism", "historic", "leisure", "amenity", "shop", "sport")


def build_query(lat: float, lon: float, radius_km: int, interests: list[Interest]) -> str:
    clauses = list(ALWAYS)
    for i in sorted(interests):  # order-independent query text -> one cache key per interest set
        clauses += FILTERS.get(i, [])
    around = f"(around:{radius_km * 1000},{lat:.5f},{lon:.5f})"
    body = "\n".join(f'  nwr{c}["name"]{around};' for c in dict.fromkeys(clauses))
    return f"[out:json][timeout:25];\n(\n{body}\n);\nout center tags;"


# Many OSM POIs carry only tourism=attraction; their English name is the one remaining signal.
NAME_HINTS: dict[str, tuple[str, ...]] = {
    "waterfalls": (" falls", " fall", "waterfall"),
    "nature": (" falls", "waterfall", " lake", " park", " peak", " hill", "canyon", " cave", "garden"),
    "heritage": ("museum", " fort", "palace", "heritage", "memorial"),
    "religious": ("temple", "church", "cathedral", "mosque", "monastery", "gurudwara", "mandir", "dargah"),
}


def _interests(tags: dict, name: str) -> list[str]:
    padded = f" {name.lower()}"
    return [k for k, m in MATCHERS.items()
            if m(tags) or any(h in padded for h in NAME_HINTS.get(k, ()))]


def _kind(tags: dict) -> str:
    for k in KIND_KEYS:
        v = tags.get(k)
        if v and not (k == "tourism" and v == "attraction"):  # prefer a more specific tag when present
            return k if v == "yes" else v
    return "attraction"


def _wiki(tags: dict) -> tuple[str | None, str | None]:
    if w := tags.get("wikipedia"):
        lang, _, title = w.partition(":")
        if title:
            return f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}", w
    if q := tags.get("wikidata"):
        return f"https://www.wikidata.org/wiki/{q}", q
    return None, None


def to_place(el: dict, retrieved_at: datetime) -> Place | None:
    tags = el.get("tags") or {}
    name = tags.get("name:en") or tags.get("name")
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lon = el.get("lon") or (el.get("center") or {}).get("lon")
    if not name or lat is None or lon is None:
        return None
    wiki_url, wiki_tag = _wiki(tags)
    fee = {"yes": True, "no": False}.get(tags.get("fee", ""))
    osm_type, osm_id = el["type"], el["id"]
    return Place(
        id=f"osm:{osm_type}/{osm_id}", name=name, kind=_kind(tags),
        interests=_interests(tags, name),
        lat=float(lat), lon=float(lon), opening_hours=tags.get("opening_hours"),
        wiki_url=wiki_url, wiki_tag=wiki_tag, fee=fee,
        source=Source(name="OpenStreetMap", url=f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
                      retrieved_at=retrieved_at),
        confidence=Confidence.verified,
    )


def dedupe(places: list[Place], meters: float = 50) -> list[Place]:
    out: list[Place] = []
    for p in places:
        if not any(q.name.lower() == p.name.lower() and haversine_km(p.lat, p.lon, q.lat, q.lon) * 1000 <= meters
                   for q in out):
            out.append(p)
    return out


async def _overpass(query: str) -> dict:
    """One attempt per public instance: the alternate instance is the retry (keeps worst case ~1 min)."""
    for url in get_settings().overpass_urls:
        try:
            return await request_once("overpass", "POST", url, data={"data": query})
        except Exception as e:  # noqa: BLE001 - try the next public instance
            log.warning("overpass %s failed: %s %s", url, type(e).__name__, str(e)[:120])
    raise AppError("overpass_unavailable", "The OpenStreetMap attractions service is not responding",
                   "Public Overpass servers are busy; please try again in a minute.", 503)


def _parse(res: dict) -> list[Place]:
    at = datetime.fromisoformat(res["retrieved_at"])  # kept on every Place, so stale data shows its real age
    return dedupe([p for el in res["data"].get("elements", []) if (p := to_place(el, at))])


def relevant_count(places: list[Place], interests: list[Interest]) -> int:
    """Places that match a requested interest (all named places when no interest was given)."""
    wanted = {i.value for i in interests}
    return sum(1 for p in places if not wanted or wanted & set(p.interests))


async def _stale_places(center: GeoPoint, interests: list[Interest], radii: tuple[int, ...],
                        min_places: int) -> list[Place] | None:
    """Best expired cache entry over the given radii: the first with enough places, else the richest."""
    best: list[Place] = []
    for r in radii:
        res = await cache.lookup_stale("overpass", {"q": build_query(center.lat, center.lon, r, interests)})
        places = _parse(res) if res else []
        if relevant_count(places, interests) >= min_places:
            return places
        best = max(best, places, key=lambda ps: relevant_count(ps, interests))
    return best or None


async def find_places(center: GeoPoint, interests: list[Interest], radius_km: int | None = None,
                      min_places: int = MIN_PLACES) -> list[Place]:
    """Query at 5, 15, 30, 60 km (or one fixed radius) until min_places places match the requested interests.

    Callers pass the trip's need (days x stops per day + spare), so longer trips search wider.

    If every Overpass instance is down, fall back to the last cached result for any radius (even expired);
    with no cached result either, raise AppError("overpass_unavailable")."""
    radii = (radius_km,) if radius_km else RADII_KM
    places: list[Place] = []
    for r in radii:
        q = build_query(center.lat, center.lon, r, interests)

        async def fetch(q: str = q) -> dict:
            return {"data": await _overpass(q), "retrieved_at": datetime.now(UTC).isoformat()}

        try:
            res = await cache.get_or_fetch("overpass", {"q": q}, fetch, timedelta(days=7))
        except AppError as e:
            if e.type != "overpass_unavailable":
                raise
            stale = await _stale_places(center, interests, radii, min_places)
            if stale is None:
                raise
            log.warning("overpass down: using cached places from %s", stale[0].source.retrieved_at)
            return stale
        places = _parse(res)
        found = relevant_count(places, interests)
        log.info("overpass radius=%dkm -> %d places, %d relevant (need %d)", r, len(places), found, min_places)
        if found >= min_places:
            break
    return places
