from datetime import timedelta

import pytest

from app.agents import aggregator, budget
from app.core.errors import AppError
from app.schemas.place import GeoPoint, Place, Source
from app.schemas.plan import DayPlan, Leg, Stop, TransportOption
from app.schemas.trip import TripRequest

from ..fakes import COST_ROWS, START, install_repo

SRC = Source(name="t", url="https://osm.test/1")
GEO = GeoPoint(name="Shillong", lat=25.5, lon=91.9, tier=2, source=SRC, confidence="verified")
PLACE = Place(id="osm:node/1", name="Museum X", kind="museum", lat=25.5, lon=91.9, source=SRC, confidence="verified")
LEG = Leg(km=1.0, minutes=5, source=Source(name="OSRM"), confidence="verified")


def req(budget_inr=8000, days=3):
    return TripRequest(origin="Guwahati", destination="Shillong", start_date=START,
                       end_date=START + timedelta(days=days - 1), travellers=3, budget_inr=budget_inr)


def days(n=3):
    return [DayPlan(date=START + timedelta(days=i), window_start="09:00", window_end="20:00",
                    stops=[Stop(place_id=PLACE.id, arrive="10:00", depart="11:00", leg_from_prev=LEG)][: int(i == 0)],
                    end_time="12:00") for i in range(n)]


OPTIONS = [TransportOption(mode=m, km=100, minutes=180, total_inr=t, description=m, leg_source=SRC, source=SRC,
                           confidence="estimated") for m, t in (("state_bus", 900), ("private_cab", 3200))]


def state(**kw):
    s = {"request": req(), "dest_geo": GEO, "days": days(), "places": [PLACE], "intercity_options": OPTIONS}
    s.update(kw)
    return s


def test_breakdown_arithmetic_is_exact():
    bd = budget.build_breakdown(req(), days(), {PLACE.id: PLACE}, OPTIONS, COST_ROWS, dict(budget.INITIAL), "v1")
    assert bd.grand_total == sum(line.total for line in bd.lines)
    assert all(line.total == line.unit_cost * line.qty for line in bd.lines)
    assert bd.per_person == bd.grand_total // 3
    cats = [line.category for line in bd.lines]
    assert cats[:4] == ["transport", "stay", "food", "local_transport"] and "activities" in cats
    stay = bd.lines[1]
    assert stay.qty == 2 * 2  # 2 nights x ceil(3/2) rooms
    assert bd.lines[2].qty == 3 * 3 * 3  # 3 meals x 3 days x 3 people
    assert bd.transport_mode == "state_bus"  # cheapest within 8 h


def test_downgrade_order():
    dg = dict(budget.INITIAL, transport_mode="private_cab")
    dg, what = budget.next_downgrade(dg, OPTIONS)
    assert what == "stay" and dg["stay_style"] == "budget"
    dg, what = budget.next_downgrade(dg, OPTIONS)
    assert what == "transport" and dg["transport_mode"] == "state_bus"
    dg, what = budget.next_downgrade(dg, OPTIONS)
    assert what == "paid_activities" and dg["include_paid"] is False
    assert budget.next_downgrade(dg, OPTIONS) is None


async def test_over_budget_loops_then_flags_gap(monkeypatch):
    install_repo(monkeypatch)
    out = await budget.budget_node(state(request=req(budget_inr=100)))
    assert out["budget_loop"] and out["retries"] == 1 and out["downgrade"]["stay_style"] == "budget"
    out2 = await budget.budget_node(state(request=req(budget_inr=100), downgrade=out["downgrade"], retries=2))
    bd = out2["budget"]
    assert not out2["budget_loop"] and bd.within_budget is False and bd.gap_inr == bd.grand_total - 100
    assert bd.suggestions[0] == f"Over budget by Rs {bd.gap_inr}."


async def test_within_budget_no_loop(monkeypatch):
    install_repo(monkeypatch)
    out = await budget.budget_node(state(request=req(budget_inr=10**6)))
    assert out["within_budget"] and not out["budget_loop"] and out["budget"].gap_inr == 0


async def test_no_cost_rows_marks_unavailable(monkeypatch):
    install_repo(monkeypatch, rows=[])
    out = await budget.budget_node(state(request=req(budget_inr=10**6)))
    assert all(line.confidence == "unavailable" for line in out["budget"].lines)
    assert any("aren't available" in w for w in out["warnings"])


async def _plan_state(monkeypatch):
    install_repo(monkeypatch)
    s = state(request=req(budget_inr=10**6))
    s.update(await budget.budget_node(s))
    s["origin_geo"] = GEO
    return s


async def test_aggregator_builds_plan_with_sources(monkeypatch):
    plan = aggregator.assemble(await _plan_state(monkeypatch))
    assert plan.title.startswith("Guwahati to Shillong") and plan.sources
    assert any(s.url == PLACE.source.url for s in plan.sources)


async def test_validator_catches_forged_id(monkeypatch):
    s = await _plan_state(monkeypatch)
    plan = aggregator.assemble(s)
    forged = plan.model_copy(deep=True)
    forged.days[0].stops[0].place_id = "osm:node/424242"
    with pytest.raises(AppError):
        aggregator.validate_plan(forged)
    # assemble() itself removes unknown stops with a warning instead of failing
    s["days"][0].stops[0].place_id = "osm:node/424242"
    plan2 = aggregator.assemble(s)
    assert plan2.days[0].stops == []  # removed (and logged), never shown


async def test_validator_catches_bad_total(monkeypatch):
    plan = aggregator.assemble(await _plan_state(monkeypatch))
    bad = plan.model_copy(deep=True)
    bad.budget.grand_total += 1
    with pytest.raises(AppError):
        aggregator.validate_plan(bad)


def test_paid_cut_is_partial_most_expensive_first():
    fort = PLACE.model_copy(update={"id": "osm:node/2", "kind": "fort", "name": "Fort"})
    park = PLACE.model_copy(update={"id": "osm:node/3", "kind": "park", "name": "Park"})
    stops = [Stop(place_id=p.id, arrive="10:00", depart="11:00", leg_from_prev=LEG) for p in (PLACE, fort, park)]
    d = [DayPlan(date=START, window_start="09:00", window_end="20:00", stops=stops)]
    places = {p.id: p for p in (PLACE, fort, park)}
    # fees per person in the seed: fort 100, museum 50, park 30 -> x3 travellers = 300, 150, 90
    assert budget.stops_to_cut(d, places, COST_ROWS, 3, gap=200) == ["osm:node/2"]
    assert budget.stops_to_cut(d, places, COST_ROWS, 3, gap=400) == ["osm:node/2", "osm:node/1"]
    assert budget.stops_to_cut(d, places, COST_ROWS, 3, gap=10**6) == ["osm:node/2", "osm:node/1", "osm:node/3"]


async def test_sources_are_readable_linked_and_unique(monkeypatch):
    state = await _plan_state(monkeypatch)
    museum = PLACE.model_copy(update={"source": Source(name="OpenStreetMap", url="https://osm.test/museum")})
    state["places"] = [museum]
    plan = aggregator.assemble(state)
    names = [s.name for s in plan.sources]
    assert all(s.url for s in plan.sources) and len({s.url for s in plan.sources}) == len(plan.sources)
    assert "Shillong location – OpenStreetMap" in names and "Museum X – OpenStreetMap" in names
    assert not any("Preliminary" in n or "cities table" in n for n in names)
