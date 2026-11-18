"""Geoapify fallback: OSM ids/tags preserved, key never leaks, and the source order in Research."""
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import respx

from app.agents import research
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.schemas.place import GeoPoint, Source
from app.schemas.trip import Interest, TripRequest
from app.tools import geoapify_places as geo
from app.tools import wiki_places

from .conftest import load_fixture

FIXTURE = load_fixture("geoapify_shillong_15km.json")
DEST = GeoPoint(name="Shillong", lat=25.57599, lon=91.88279, tier=2, source=Source(name="t"), confidence="verified")
REQ = TripRequest(origin="Guwahati", destination="Shillong", start_date=date.today() + timedelta(days=5),
                  end_date=date.today() + timedelta(days=6), travellers=2, budget_inr=8000,
                  interests=[Interest.nature, Interest.waterfalls])
KEY = "test-geoapify-key-123"


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(geo, "get_settings", lambda: Settings(_env_file=None, geoapify_api_key=KEY))


def test_parse_keeps_osm_ids_and_tags():
    places = geo.parse(FIXTURE, datetime.now(UTC))
    by_name = {p.name: p for p in places}
    park = by_name["Lady Hydari Park"]
    assert park.id == "osm:way/275648471" and park.kind == "park" and "nature" in park.interests
    assert park.source.url == "https://www.openstreetmap.org/way/275648471"
    assert park.source.name == "OpenStreetMap (via Geoapify)"
    assert "waterfalls" in by_name["Sweet Falls View Point"].interests  # name hint still applies


def test_place_of_worship_needs_tourism_or_wiki():
    assert not geo.keep({"amenity": "place_of_worship", "religion": "christian", "name": "Local church"})
    assert geo.keep({"amenity": "place_of_worship", "wikidata": "Q1", "name": "Famous cathedral"})
    assert geo.keep({"tourism": "attraction", "name": "Falls"})


def test_categories_are_order_independent_and_include_basics():
    a = geo.categories_for([Interest.nature, Interest.religious])
    assert a == geo.categories_for([Interest.religious, Interest.nature])
    assert a.startswith("tourism.attraction,tourism.sights,entertainment.museum")


async def test_widening_and_key_not_cached_or_logged(with_key, mem_cache):
    few = {"type": "FeatureCollection", "features": FIXTURE["features"][:2]}
    with respx.mock() as r:
        route = r.get(geo.API).mock(side_effect=[httpx.Response(200, json=few), httpx.Response(200, json=FIXTURE)])
        places = await geo.find_places(DEST, [Interest.nature], min_places=5)
    assert route.call_count == 2 and len(places) > 5
    assert route.calls[0].request.url.params["apiKey"] == KEY
    assert "circle:91.88279,25.57599,5000" in str(route.calls[0].request.url.params["filter"])
    stored = str(mem_cache.data)
    assert KEY not in stored and all(KEY not in p.source.url for p in places)


async def test_http_error_hides_url_with_key(with_key):
    with respx.mock() as r:
        r.get(geo.API).respond(401)
        with pytest.raises(AppError) as e:
            await geo.find_places(DEST, [Interest.nature])
    assert e.value.type == "geoapify_unavailable" and KEY not in str(e.value) and "401" in e.value.detail


def _overpass_down(router):
    for u in get_settings().overpass_urls:
        router.post(u).respond(502)


async def test_research_uses_geoapify_before_wikipedia(with_key):
    with respx.mock(assert_all_called=False) as r:
        _overpass_down(r)
        r.get(geo.API).respond(json=FIXTURE)
        wiki = r.get(wiki_places.API).respond(json={})
        places, warnings = await research.gather_places(DEST, REQ)
    assert places and all(p.id.startswith("osm:") for p in places)
    assert warnings == [] and wiki.call_count == 0  # same OSM data: nothing to tell the traveller


async def test_research_falls_to_wikipedia_when_geoapify_fails(with_key, monkeypatch):
    async def no_cities(*a, **k):
        return []

    monkeypatch.setattr(research.repo, "nearby_cities", no_cities)
    with respx.mock(assert_all_called=False) as r:
        _overpass_down(r)
        r.get(geo.API).respond(500)
        r.get(wiki_places.API).respond(json=load_fixture("wiki_geosearch_shillong.json"))
        r.get(url__regex=r".*rest_v1/page/summary/.*").respond(json=load_fixture("wiki_summary.json"))
        places, warnings = await research.gather_places(DEST, REQ)
    assert places and all(p.id.startswith("wiki:") for p in places) and "Fewer attractions" in warnings[0]


async def test_no_key_skips_geoapify(monkeypatch):
    monkeypatch.setattr(geo, "get_settings", lambda: Settings(_env_file=None))
    assert not geo.enabled()


async def test_any_error_hides_key(with_key):
    with respx.mock() as r:
        r.get(geo.API).mock(side_effect=ValueError(f"boom {KEY}"))
        with pytest.raises(AppError) as e:
            await geo.find_places(DEST, [Interest.nature])
    assert KEY not in str(e.value) and KEY not in e.value.detail
