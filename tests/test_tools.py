"""Offline tool tests: replay recorded live fixtures with respx (no network)."""
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.db import repo
from app.schemas.data import City
from app.schemas.place import Confidence, GeoPoint, Source
from app.schemas.trip import Interest
from app.tools import attractions, geocode, routing, weather, wiki

from .conftest import load_fixture

SHILLONG = load_fixture("nominatim_shillong.json")[0]
GUWAHATI = load_fixture("nominatim_guwahati.json")[0]


def gp(d) -> GeoPoint:
    return GeoPoint(name=d["name"], lat=float(d["lat"]), lon=float(d["lon"]), source=Source(name="t"),
                    confidence="verified")


# ---- geocode -------------------------------------------------------------
@respx.mock
async def test_geocode_hit(monkeypatch):
    monkeypatch.setattr("app.db.repo._csv_cities", lambda: [])  # force the Nominatim path
    respx.get(geocode.NOMINATIM).respond(json=[SHILLONG])
    p = await geocode.geocode("Shillong")
    assert p and p.name == "Shillong" and p.confidence == "verified"
    assert "nominatim.openstreetmap.org" in p.source.url and len(p.bbox) == 4


@respx.mock
async def test_geocode_miss_returns_none_and_caches():
    route = respx.get(geocode.NOMINATIM).respond(json=[])
    assert await geocode.geocode("Nowhereabad") is None
    assert await geocode.geocode("Nowhereabad") is None
    assert route.call_count == 1  # empty result is cached too


@respx.mock
async def test_geocode_cities_table_shortcut(monkeypatch):
    route = respx.get(geocode.NOMINATIM).respond(json=[SHILLONG])

    async def fake_city(name):
        return City(name="Shillong", state="Meghalaya", tier=2, lat=float(SHILLONG["lat"]),
                    lon=float(SHILLONG["lon"]))

    monkeypatch.setattr(repo, "get_city", fake_city)
    p = await geocode.geocode("shillong")
    assert p.tier == 2 and route.call_count == 0


# ---- attractions ---------------------------------------------------------
OVERPASS = load_fixture("overpass_shillong_waterfalls_nature.json")


def test_build_query_contains_filters():
    q = attractions.build_query(25.5, 91.9, 5, [Interest.waterfalls])
    assert '[out:json][timeout:25]' in q and '["natural"="waterfall"]' in q and "around:5000" in q
    assert 'tourism"~"^(attraction|viewpoint)$' in q and "out center tags;" in q


def test_mapping_from_fixture():
    places = [p for e in OVERPASS["elements"] if (p := attractions.to_place(e, datetime.now(UTC)))]
    assert places and all(p.id.startswith("osm:") and p.source.url.startswith("https://www.openstreetmap.org/")
                          for p in places)
    falls = next(p for p in places if p.name == "Elephant Falls")
    assert "waterfalls" in falls.interests  # name hint on a tourism=attraction node
    way = next(p for p in places if p.id.startswith("osm:way/"))
    assert way.lat and way.lon  # 'center' used for ways


def test_dedupe_same_name_within_50m():
    el = {"type": "node", "id": 1, "lat": 25.0, "lon": 91.0, "tags": {"name": "X Falls", "natural": "waterfall"}}
    near = {**el, "id": 2, "lat": 25.0002}
    far = {**el, "id": 3, "lat": 25.01}
    now = datetime.now(UTC)
    out = attractions.dedupe([attractions.to_place(e, now) for e in (el, near, far)])
    assert [p.id for p in out] == ["osm:node/1", "osm:node/3"]


@respx.mock
async def test_widening_until_enough(monkeypatch):
    url = get_settings().overpass_urls[0]
    few = {"elements": OVERPASS["elements"][:2]}
    route = respx.post(url).mock(side_effect=[httpx.Response(200, json=few), httpx.Response(200, json=OVERPASS)])
    places = await attractions.find_places(gp(SHILLONG), [Interest.waterfalls])
    assert route.call_count == 2 and len(places) >= attractions.MIN_PLACES


@respx.mock
async def test_overpass_falls_back_to_second_instance():
    urls = get_settings().overpass_urls
    respx.post(urls[0]).respond(504)
    respx.post(urls[1]).respond(json=OVERPASS)
    assert await attractions.find_places(gp(SHILLONG), [Interest.nature], radius_km=15)


# ---- routing -------------------------------------------------------------
ROUTE = load_fixture("osrm_route_guwahati_shillong.json")
TABLE = load_fixture("osrm_table_guwahati_shillong.json")


@respx.mock
async def test_route_parsing():
    respx.get(url__regex=r".*/route/v1/driving/.*").respond(json=ROUTE)
    leg = await routing.route(gp(GUWAHATI), gp(SHILLONG))
    # OSRM's car profile says ~72 min; capped at 40 km/h it is ~146 min, closer to real hill-road driving
    assert 80 < leg.km < 130 and leg.minutes == round(leg.km / 40 * 60) and leg.confidence == "estimated"
    assert "adjusted for local road" in leg.source.name
    assert leg.geometry and len(leg.geometry[0]) == 2 and "router.project-osrm.org" in leg.source.url


@respx.mock
async def test_matrix_parsing():
    respx.get(url__regex=r".*/table/v1/driving/.*").respond(json=TABLE)
    m = await routing.matrix([gp(GUWAHATI), gp(SHILLONG)])
    assert m[0][0].km == 0 and m[0][1].km > 80 and not m[0][1].estimated


@respx.mock
async def test_routing_fallback_on_error():
    respx.get(url__regex=r".*/route/v1/driving/.*").mock(side_effect=httpx.ConnectTimeout("t"))
    leg = await routing.route(gp(GUWAHATI), gp(SHILLONG))
    assert leg.confidence == Confidence.estimated and leg.source.name.startswith("Straight-line")
    assert leg.km > 50 and leg.minutes == round(leg.km / 30 * 60)


# ---- weather -------------------------------------------------------------
FC = load_fixture("openmeteo_forecast_shillong.json")
AR = load_fixture("openmeteo_archive_shillong.json")


@respx.mock
async def test_forecast_inside_horizon():
    respx.get(weather.FORECAST_URL).respond(json=FC)
    start = date.fromisoformat(FC["daily"]["time"][0])
    days = await weather.daily_weather(gp(SHILLONG), start, start + timedelta(days=2), today=start)
    assert [d.kind for d in days] == ["forecast"] * 3 and days[0].label in weather.WMO.values()
    assert "Open-Meteo" in days[0].source.name


@respx.mock
async def test_split_at_horizon():
    respx.get(weather.FORECAST_URL).respond(json=FC)
    ar = respx.get(weather.ARCHIVE_URL).respond(json=AR)
    today = date.fromisoformat(FC["daily"]["time"][0]) - timedelta(days=13)  # horizon = today+14 (1 safety day)
    start = today + timedelta(days=13)
    days = await weather.daily_weather(gp(SHILLONG), start, start + timedelta(days=2), today=today)
    kinds = [d.kind for d in days]
    assert kinds[:2] == ["forecast", "forecast"] and kinds[-1] == "climate_normal"
    assert ar.call_count == weather.NORMAL_YEARS
    assert days[-1].confidence == "estimated" and "typical" in days[-1].source.name


def test_is_rainy():
    w = weather.DayWeather(date=date.today(), kind="forecast", label="Moderate rain", source=Source(name="x"),
                           confidence="verified")
    assert weather.is_rainy(w)
    assert not weather.is_rainy(w.model_copy(update={"label": "Clear sky", "precip_prob": 10}))


# ---- wiki ----------------------------------------------------------------
@respx.mock
async def test_wiki_summary_truncated():
    respx.get(url__regex=r"https://en\.wikipedia\.org/api/rest_v1/page/summary/.*").respond(
        json=load_fixture("wiki_summary.json"))
    s = await wiki.summary("en:Shillong")
    assert s and len(s["extract"]) <= wiki.MAX_CHARS and s["url"].startswith("https://en.wikipedia.org/")


@respx.mock
async def test_wiki_failure_returns_none():
    respx.get(url__regex=r".*wikipedia.*").respond(404)
    assert await wiki.summary("en:Nope") is None


@pytest.mark.parametrize("tag", ["", "Q"])
async def test_wiki_bad_tags(tag):
    with respx.mock(assert_all_called=False):
        respx.get(url__regex=r".*").respond(404)
        assert await wiki.summary(tag) is None


@respx.mock
async def test_overpass_outage_is_clear_error_with_one_try_per_instance():
    from app.core.errors import AppError
    urls = get_settings().overpass_urls
    routes = [respx.post(urls[0]).mock(side_effect=httpx.ReadTimeout(""))]  # empty message must not crash
    routes += [respx.post(u).respond(502) for u in urls[1:]]
    with pytest.raises(AppError) as e:
        await attractions.find_places(gp(SHILLONG), [Interest.nature], radius_km=5)
    assert e.value.type == "overpass_unavailable"
    assert [r.call_count for r in routes] == [1] * len(routes)  # no retry storm


def test_query_independent_of_interest_order():
    a = attractions.build_query(25.5, 91.9, 5, [Interest.nature, Interest.waterfalls])
    assert a == attractions.build_query(25.5, 91.9, 5, [Interest.waterfalls, Interest.nature])


def test_realistic_minutes_caps_optimistic_osrm_times():
    assert routing.realistic_minutes(9.5, 8) == (23, True)  # town leg: 25 km/h, not 71 km/h
    assert routing.realistic_minutes(97.2, 72) == (146, True)  # Guwahati-Shillong road: 40 km/h
    assert routing.realistic_minutes(10.0, 40) == (40, False)  # already slower: OSRM kept


async def test_forecast_window_keeps_a_safety_day():
    """Regression: on 19 Nov (India) a 2-4 Dec request asked Open-Meteo for 4 Dec, one day past its UTC window."""
    with respx.mock(assert_all_called=False) as r:
        fc = r.get(weather.FORECAST_URL).respond(json=FC)
        r.get(weather.ARCHIVE_URL).respond(json=AR)
        await weather.daily_weather(gp(SHILLONG), date(2026, 12, 2), date(2026, 12, 4), today=date(2026, 11, 19))
    assert fc.calls[0].request.url.params["end_date"] == "2026-12-03"


async def test_rejected_forecast_falls_back_to_typical_weather():
    with respx.mock(assert_all_called=False) as r:
        r.get(weather.FORECAST_URL).respond(400, json={"error": True, "reason": "out of allowed range"})
        r.get(weather.ARCHIVE_URL).respond(json=AR)
        start = date.today() + timedelta(days=3)
        days = await weather.daily_weather(gp(SHILLONG), start, start + timedelta(days=2))
    assert days and all(d.kind == "climate_normal" for d in days)
