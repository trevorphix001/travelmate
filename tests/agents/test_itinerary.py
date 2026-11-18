from datetime import timedelta

import respx

from app.agents import itinerary
from app.schemas.place import GeoPoint, Place, Source
from app.schemas.plan import DayWeather
from app.schemas.trip import TripRequest

from ..fakes import START, FakeLLM

CENTRE = GeoPoint(name="Town", lat=25.5, lon=91.9, source=Source(name="t"), confidence="verified")


def place(i: int, kind: str = "museum", dlat: float = 0.0, interests=None, hours=None) -> Place:
    return Place(id=f"osm:node/{i}", name=f"Place {i}", kind=kind, interests=interests or [], lat=25.5 + dlat,
                 lon=91.9 + 0.01 * i, opening_hours=hours, source=Source(name="OpenStreetMap", url="https://osm.test"),
                 confidence="verified")


def req(days=2, pace="normal") -> TripRequest:
    return TripRequest(origin="A city", destination="Town", start_date=START,
                       end_date=START + timedelta(days=days - 1), travellers=2, budget_inr=9000, pace=pace)


def wx(d, label):
    return DayWeather(date=d, kind="forecast", label=label, source=Source(name="w"), confidence="verified")


async def build(monkeypatch, llm, cands, weather=(), r=None):
    llm.install(monkeypatch)
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*").respond(503)  # OSRM down -> labelled straight-line legs
        return await itinerary.build_days(r or req(), cands, list(weather), CENTRE, [], None, None, "A city")


async def test_unknown_and_duplicate_ids_dropped(monkeypatch):
    cands = [place(i) for i in range(4)]
    llm = FakeLLM()
    llm.itin = lambda p: {"days": [{"date": START.isoformat(), "place_ids": ["osm:node/0", "osm:node/0", "fake:1"]},
                                   {"date": (START + timedelta(days=1)).isoformat(), "place_ids": ["osm:node/1"]}]}
    days, _, _ = await build(monkeypatch, llm, cands)
    ids = [s.place_id for d in days for s in d.stops]
    assert "fake:1" not in ids and len(ids) == len(set(ids))  # unknown dropped, no duplicates (refill may add)
    assert "osm:node/0" in [s.place_id for s in days[0].stops]
    assert "osm:node/1" in [s.place_id for s in days[1].stops]


async def test_every_leg_has_source_and_times_are_ordered(monkeypatch):
    days, _, _ = await build(monkeypatch, FakeLLM(), [place(i) for i in range(6)])
    for d in days:
        assert d.return_leg and d.return_leg.source.name
        for s in d.stops:
            assert s.leg_from_prev.source.name and s.leg_from_prev.confidence == "estimated"
            assert s.arrive < s.depart
        assert d.end_time <= d.window_end


async def test_overfull_day_trimmed(monkeypatch):
    far = [place(i, kind="nature_reserve", dlat=0.05 * (i + 1)) for i in range(4)]  # 2h visits, nearby
    llm = FakeLLM()
    llm.itin = lambda p: {"days": [{"date": START.isoformat(), "place_ids": [c["id"] for c in p["candidates"]]}]}
    days, _, _ = await build(monkeypatch, llm, far, r=req(days=1))
    assert len(days[0].stops) < 4 and any("not enough time" in n for n in days[0].notes)
    assert days[0].end_time <= days[0].window_end


async def test_closed_place_dropped(monkeypatch):
    cands = [place(1, hours="06:00-08:00")]
    llm = FakeLLM()
    llm.itin = lambda p: {"days": [{"date": START.isoformat(), "place_ids": ["osm:node/1"]}]}
    days, _, _ = await build(monkeypatch, llm, cands, r=req(days=1))
    assert days[0].stops == [] and any("closed" in n for n in days[0].notes)


async def test_rain_day_moves_waterfall(monkeypatch):
    fall = place(1, kind="waterfall", interests=["waterfalls"])
    museum = place(2)
    llm = FakeLLM()
    llm.itin = lambda p: {"days": [{"date": START.isoformat(), "place_ids": [fall.id, museum.id]},
                                   {"date": (START + timedelta(days=1)).isoformat(), "place_ids": []}]}
    weather = [wx(START, "Heavy rain"), wx(START + timedelta(days=1), "Clear sky")]
    days, _, _ = await build(monkeypatch, llm, [fall, museum], weather)
    assert [s.place_id for s in days[0].stops] == [museum.id]
    assert [s.place_id for s in days[1].stops] == [fall.id]


async def test_llm_failure_falls_back(monkeypatch):
    llm = FakeLLM()
    llm.fail = {"ItinOut"}
    days, trace, warnings = await build(monkeypatch, llm, [place(i) for i in range(5)])
    assert sum(len(d.stops) for d in days) >= 2 and trace == [] and warnings == []  # fallback is silent


async def test_notes_with_numbers_are_dropped(monkeypatch):
    llm = FakeLLM()
    llm.itin = lambda p: {"days": [{"date": START.isoformat(), "place_ids": ["osm:node/1"]}],
                          "notes": {START.isoformat(): "Entry costs Rs 500"}}
    days, _, _ = await build(monkeypatch, llm, [place(1)], r=req(days=1))
    assert not any("500" in n for n in days[0].notes)


def test_opening_window_parser():
    assert itinerary.opening_window("Mo-Su 09:00-17:30") == (540, 1050)
    assert itinerary.opening_window("24/7") == (0, 1440)
    assert itinerary.opening_window("sunrise-sunset") is None


async def test_road_time_shifts_day_one_without_fares(monkeypatch):
    FakeLLM().install(monkeypatch)
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r".*").respond(503)
        days, _, _ = await itinerary.build_days(req(), [place(1)], [], CENTRE, [], None, None, "A city",
                                                road_minutes=150)
    assert days[0].window_start == "11:30" and days[-1].window_end == "17:30"
    assert any("by road" in n for n in days[0].notes)


async def test_driving_budget_drops_farthest_stop_first(monkeypatch):
    near = [place(i) for i in range(3)]  # all within ~3 km of the centre
    far = place(9, dlat=0.9)  # ~100 km away: a 5 h round trip at realistic speeds
    llm = FakeLLM()
    llm.itin = lambda p: {"days": [{"date": START.isoformat(), "place_ids": [far.id] + [x.id for x in near]}]}
    days, _, _ = await build(monkeypatch, llm, [far, *near], r=req(days=1))
    ids = [s.place_id for s in days[0].stops]
    assert far.id not in ids and len(ids) == 3
    assert any("too far to fit" in n for n in days[0].notes)
    drive = sum(s.leg_from_prev.minutes for s in days[0].stops) + days[0].return_leg.minutes
    assert drive <= itinerary.MAX_DRIVE_MIN[itinerary.Pace.normal]


def test_arrival_and_departure_days_get_less_driving():
    from app.schemas.trip import Pace
    assert itinerary.drive_budget(Pace.normal, 0) == 240
    assert itinerary.drive_budget(Pace.normal, 190) == 60  # a 3 h bus ride leaves only a short local loop
    assert itinerary.drive_budget(Pace.relaxed, 2 * 190) == itinerary.MIN_LOCAL_DRIVE


async def test_trimmed_day_is_refilled_with_reachable_places(monkeypatch):
    far = [place(i, dlat=0.9 + 0.01 * i) for i in range(2)]  # ~100 km: dropped by the driving budget
    near = [place(10 + i) for i in range(3)]  # unused by the LLM, but close to the stay
    llm = FakeLLM()
    llm.itin = lambda p: {"days": [{"date": START.isoformat(), "place_ids": [x.id for x in far]}],
                          "notes": {START.isoformat(): "Remote reserves"}}
    days, _, _ = await build(monkeypatch, llm, [*far, *near], r=req(days=1))
    ids = {s.place_id for s in days[0].stops}
    assert ids and ids <= {x.id for x in near}
    assert any("too far" in n for n in days[0].notes)


async def test_refill_removes_stale_dropped_note(monkeypatch):
    far = place(1, dlat=0.9)
    near = place(2)
    llm = FakeLLM()
    llm.itin = lambda p: {"days": [{"date": START.isoformat(), "place_ids": [far.id, near.id, place(3).id]}]}
    days, _, _ = await build(monkeypatch, llm, [far, near, place(3)], r=req(days=1))
    kept = {s.place_id for s in days[0].stops}
    names = {p.name for p in (far, near, place(3)) if p.id in kept}
    assert not any(n.startswith(f"Dropped {nm}:") for n in days[0].notes for nm in names)
