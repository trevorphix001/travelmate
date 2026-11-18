"""Live recorders: call each real API once and save raw JSON to tests/fixtures/.

Run once with: pytest -m live tests/test_live_record.py -s
Offline tests replay these fixtures with respx.
"""
from datetime import date, timedelta

import httpx
import pytest

from app.core.config import get_settings
from app.tools import attractions, weather
from app.tools.geocode import NOMINATIM

from .conftest import save_fixture

pytestmark = pytest.mark.live
UA = {"User-Agent": get_settings().app_user_agent}
# Coordinates of the two demo cities, taken from the Nominatim fixtures recorded below.
GEO = {}


async def _get(url, params=None):
    async with httpx.AsyncClient(timeout=60, headers=UA) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        return r.json()


async def test_record_nominatim():
    for city in ("Shillong", "Guwahati"):
        data = await _get(NOMINATIM, {"q": city, "format": "jsonv2", "limit": 1, "addressdetails": 0,
                                      "countrycodes": "in"})
        save_fixture(f"nominatim_{city.lower()}.json", data)
        GEO[city] = (float(data[0]["lat"]), float(data[0]["lon"]))


async def test_record_overpass():
    lat, lon = GEO.get("Shillong") or (None, None)
    assert lat, "run test_record_nominatim first (same session)"
    q = attractions.build_query(lat, lon, 15, [attractions.Interest.waterfalls, attractions.Interest.nature])
    async with httpx.AsyncClient(timeout=90, headers=UA) as c:
        r = await c.post(get_settings().overpass_urls[0], data={"data": q})
        r.raise_for_status()
        save_fixture("overpass_shillong_waterfalls_nature.json", r.json())


async def test_record_osrm():
    (alat, alon), (blat, blon) = GEO["Guwahati"], GEO["Shillong"]
    base = get_settings().osrm_url
    coords = f"{alon:.5f},{alat:.5f};{blon:.5f},{blat:.5f}"
    save_fixture("osrm_route_guwahati_shillong.json", await _get(
        f"{base}/route/v1/driving/{coords}", {"overview": "false", "steps": "true", "geometries": "geojson"}))
    save_fixture("osrm_table_guwahati_shillong.json", await _get(
        f"{base}/table/v1/driving/{coords}", {"annotations": "duration,distance"}))


async def test_record_open_meteo():
    lat, lon = GEO["Shillong"]
    s = date.today() + timedelta(days=3)
    save_fixture("openmeteo_forecast_shillong.json", await _get(weather.FORECAST_URL, {
        "latitude": round(lat, 4), "longitude": round(lon, 4), "timezone": "Asia/Kolkata",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "start_date": s.isoformat(), "end_date": (s + timedelta(days=2)).isoformat()}))
    save_fixture("openmeteo_archive_shillong.json", await _get(weather.ARCHIVE_URL, {
        "latitude": round(lat, 4), "longitude": round(lon, 4), "timezone": "Asia/Kolkata",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
        "start_date": "2025-03-10", "end_date": "2025-03-12"}))


async def test_record_wikipedia():
    save_fixture("wiki_summary.json", await _get("https://en.wikipedia.org/api/rest_v1/page/summary/Shillong"))


def test_print_fixture_place_names():
    from datetime import UTC, datetime

    from .conftest import load_fixture
    els = load_fixture("overpass_shillong_waterfalls_nature.json")["elements"]
    places = attractions.dedupe([p for e in els if (p := attractions.to_place(e, datetime.now(UTC)))])
    for p in places:
        print(p.id, "|", p.name, "|", p.kind, "|", p.interests)  # noqa: T201 - manual check of recorded names
    assert places
