"""Geocoding: cities table first, then Nominatim (max 1 req/s, identifying User-Agent, cached 30 days)."""
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from app.core import cache
from app.db import repo
from app.schemas.place import Confidence, GeoPoint, Source
from app.tools.http import get_json

log = logging.getLogger(__name__)
NOMINATIM = "https://nominatim.openstreetmap.org/search"


def _osm_point_url(lat: float, lon: float) -> str:
    return f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=12/{lat:.5f}/{lon:.5f}"


async def _nominatim(query: str, country: str | None) -> dict | None:
    params = {"q": query, "format": "jsonv2", "limit": 1, "addressdetails": 0}
    if country:
        params["countrycodes"] = country

    async def fetch() -> dict:
        # Wrapped in a dict so an empty result is cached too (cache skips None).
        return {"results": await get_json("nominatim", NOMINATIM, params), "url": f"{NOMINATIM}?{urlencode(params)}"}

    return await cache.get_or_fetch("nominatim", params, fetch, timedelta(days=30))


async def geocode(query: str, country: str | None = "in") -> GeoPoint | None:
    city = await repo.get_city(query)
    if city:
        return GeoPoint(
            name=city.name, lat=city.lat, lon=city.lon, tier=city.tier,
            source=Source(name="TravelMate cities table (coordinates from OpenStreetMap)",
                          url=_osm_point_url(city.lat, city.lon)),
            confidence=Confidence.verified,
        )
    data = await _nominatim(query, country)
    if not data or not data["results"]:
        return None
    top = data["results"][0]
    bbox = [float(x) for x in top["boundingbox"]] if top.get("boundingbox") else None
    return GeoPoint(
        name=top.get("name") or query, lat=float(top["lat"]), lon=float(top["lon"]), bbox=bbox, tier=None,
        source=Source(name="OpenStreetMap Nominatim", url=data["url"], retrieved_at=datetime.now(UTC)),
        confidence=Confidence.verified,
    )
