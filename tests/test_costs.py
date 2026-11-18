from datetime import date

from app.schemas.data import CostRow
from app.tools import costs


def row(cat, item, typ, unit="x", tier=2, lo=None, hi=None):
    return CostRow(version="t", city_tier=tier, category=cat, item=item, unit=unit, min_inr=lo or typ,
                   typical_inr=typ, max_inr=hi or typ, source_note="test row", source_url="https://example.test/r",
                   checked_on=date(2026, 1, 1))


ROWS = [
    row("stay", "budget", 1200, "room_night", lo=800, hi=1800),
    row("stay", "budget", 1500, "room_night", tier=0),  # tier-specific row must win
    row("meal", "budget", 150, "person_meal"),
    row("intercity", "state_bus", 150, "person_100km", tier=0),
    row("intercity", "private_cab", 1400, "vehicle_100km", tier=0),
    row("duration_factor", "state_bus", 130, "percent_of_car_time", tier=0),
    row("local_transport", "budget", 600, "group_day"),
    row("entry_fee", "default", 50, "person"),
]


def test_rooms():
    assert [costs.rooms_for(n) for n in (1, 2, 3, 5)] == [1, 1, 2, 3]


def test_stay_tier_specific_and_exact():
    line = costs.stay_cost(ROWS, "budget", nights=2, rooms=2)
    assert (line.unit_cost, line.qty, line.total) == (1200, 4, 4800)
    assert line.confidence == "estimated" and line.source.url == "https://example.test/r"
    assert "Rs 800–1,800 per room per night" in line.description and "1 room × 2 nights" not in line.description


def test_intercity_person_vs_vehicle():
    bus = costs.intercity_cost(ROWS, "state_bus", km=100, people=3)
    assert (bus.unit_cost, bus.qty, bus.total) == (150, 6, 900)
    cab = costs.intercity_cost(ROWS, "private_cab", km=100, people=5)
    assert (cab.unit_cost, cab.qty, cab.total) == (1400, 4, 5600)  # 2 cabs x 2 ways


def test_meals_local_entry_and_factor():
    assert costs.meal_cost(ROWS, 2, 9).total == 2700
    assert costs.local_transport_cost(ROWS, 3).total == 1800
    assert costs.entry_fee(ROWS, "museum", 2, "X").total == 100  # falls back to default row
    assert costs.duration_factor(ROWS, "state_bus") == 1.3
    assert costs.duration_factor(ROWS, "train_sleeper") is None


def test_unavailable_when_no_row():
    line = costs.stay_cost([], "budget", 2, 1)
    assert line.total == 0 and line.confidence == "unavailable"
    assert costs.intercity_cost([], "train_sleeper", 100, 2).confidence == "unavailable"


def test_place_specific_fee_beats_kind_and_default():
    rows = ROWS + [row("entry_fee", "museum", 80, "person"), row("entry_fee", "osm:node/42", 500, "person")]
    assert costs.entry_fee(rows, "museum", 2, "X", "osm:node/42").total == 1000  # place row wins
    assert costs.entry_fee(rows, "museum", 2, "Y", "osm:node/7").total == 160  # kind row next
    assert costs.entry_fee(rows, "fort", 2, "Z", "osm:node/7").total == 100  # then default
