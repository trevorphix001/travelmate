"""Overpass outage handling: stale cache first, then Wikipedia GeoSearch."""
from datetime import UTC, date, datetime, timedelta

import pytest
import respx

from app.agents import research
from app.core import cache
from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.place import GeoPoint, Source
from app.schemas.trip import Interest, TripRequest
from app.tools import attractions, wiki_places

from .conftest import load_fixture

DEST = GeoPoint(name="Shillong", lat=25.57599, lon=91.88279, tier=2, source=Source(name="t"), confidence="verified")
OVERPASS = load_fixture("overpass_shillong_waterfalls_nature.json")
GEOSEARCH = load_fixture("wiki_geosearch_shillong.json")
INTERESTS = [Interest.waterfalls, Interest.nature]
REQ = TripRequest(origin="Guwahati", destination="Shillong", start_date=date.today() + timedelta(days=5),
                  end_date=date.today() + timedelta(days=6), travellers=2, budget_inr=8000, interests=INTERESTS)


def overpass_down(router):
    for u in get_settings().overpass_urls:
        router.post(u).respond(502)


async def put_expired(radius: int, retrieved_at: datetime) -> None:
    q = attractions.build_query(DEST.lat, DEST.lon, radius, INTERESTS)
    await cache.store("overpass", {"q": q}, {"data": OVERPASS, "retrieved_at": retrieved_at.isoformat()},
                      timedelta(seconds=-1))  # already expired


async def test_stale_cache_used_when_overpass_down():
    old = datetime.now(UTC) - timedelta(days=30)
    await put_expired(15, old)
    with respx.mock(assert_all_called=False) as r:
        overpass_down(r)
        places = await attractions.find_places(DEST, INTERESTS)
    assert len(places) >= attractions.MIN_PLACES
    assert all(p.id.startswith("osm:") and p.source.retrieved_at == old for p in places)


async def test_no_cache_still_raises():
    with respx.mock(assert_all_called=False) as r:
        overpass_down(r)
        with pytest.raises(AppError) as e:
            await attractions.find_places(DEST, INTERESTS)
    assert e.value.type == "overpass_unavailable"


def test_wikipedia_classifier_keeps_only_attractions():
    now = datetime.now(UTC)
    places = [p for pg in GEOSEARCH["query"]["pages"] if (p := wiki_places.to_place(pg, now))]
    names = {p.name for p in places}
    assert {"Elephant Falls", "Sweet Falls", "Umiam Lake"} <= names
    assert not names & {"North-Eastern Hill University", "Shillong", "Shillong Lok Sabha constituency", "Mawlai"}
    falls = next(p for p in places if p.name == "Elephant Falls")
    assert falls.kind == "waterfall" and "waterfalls" in falls.interests and falls.id == "wiki:en/63011110"
    assert falls.source.name == "Wikipedia: Elephant Falls" and falls.source.url.startswith("https://en.wikipedia.org/wiki/")


async def test_research_falls_back_to_wikipedia():
    with respx.mock(assert_all_called=False) as r:
        overpass_down(r)
        r.get(wiki_places.API).respond(json=GEOSEARCH)
        r.get(url__regex=r".*rest_v1/page/summary/.*").respond(json=load_fixture("wiki_summary.json"))
        places, warnings = await research.gather_places(DEST, REQ)
    assert places and all(p.id.startswith("wiki:") for p in places)
    assert "Fewer attractions" in warnings[0]


async def test_research_warns_on_stale_osm():
    await put_expired(5, datetime.now(UTC) - timedelta(days=30))
    with respx.mock(assert_all_called=False) as r:
        overpass_down(r)
        places, warnings = await research.gather_places(DEST, REQ)
    assert places[0].id.startswith("osm:") and "last updated" in warnings[0]


async def test_research_no_warning_when_live():
    with respx.mock(assert_all_called=False) as r:
        for u in get_settings().overpass_urls:
            r.post(u).respond(json=OVERPASS)
        places, warnings = await research.gather_places(DEST, REQ)
    assert places and warnings == []


async def test_both_sources_down_is_one_clear_error():
    with respx.mock(assert_all_called=False) as r:
        overpass_down(r)
        r.get(wiki_places.API).respond(403)
        with pytest.raises(AppError) as e:
            await research.gather_places(DEST, REQ)
    assert e.value.type == "attractions_unavailable" and "APP_USER_AGENT" in e.value.detail
