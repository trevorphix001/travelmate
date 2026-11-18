"""Cost estimates from the curated cost table. Pure Python: no network, no LLM, integer rupees.

Row conventions (see data/README.md):
  stay/<budget|mid|premium>              unit room_night
  meal/<budget|mid>                       unit person_meal
  intercity/<mode>                        unit person_100km | vehicle_100km
  duration_factor/<mode>                  unit percent_of_car_time (not money; scales OSRM car time)
  local_transport/<budget|mid>            unit group_day
  entry_fee/<place id | osm kind | default>  unit person  (a place id, e.g. osm:node/123 or wiki:en/456,
                                          beats the kind row, which beats default)
"""
import math

from app.schemas.data import CostRow
from app.schemas.place import Confidence, Source
from app.schemas.plan import BudgetLine

VEHICLE_SEATS = 4  # a hatchback/sedan cab seats four passengers


def rooms_for(travellers: int) -> int:
    return math.ceil(travellers / 2)


def find(rows: list[CostRow], category: str, item: str) -> CostRow | None:
    hits = [r for r in rows if r.category == category and r.item == item]
    # A tier-specific row beats a tier-0 ("every tier") row.
    return max(hits, key=lambda r: r.city_tier) if hits else None


def items(rows: list[CostRow], category: str) -> list[str]:
    return sorted({r.item for r in rows if r.category == category})


PER_UNIT = {"room_night": "per room per night", "person_meal": "per meal", "person_100km": "per person per 100 km",
            "vehicle_100km": "per vehicle per 100 km", "group_day": "per day", "person": "per person"}


def plural(n: int, word: str) -> str:
    many = {"person": "people"}.get(word, word + "s")
    return f"{n} {word}" if n == 1 else f"{n} {many}"


def _line(category: str, what: str, row: CostRow | None, unit_cost: int, qty: int) -> BudgetLine:
    if row is None:
        return BudgetLine(category=category, description=f"{what} (price not available)", unit_cost=0, qty=qty,
                          total=0, source=Source(name="Price not available"), confidence=Confidence.unavailable)
    per = PER_UNIT.get(row.unit, "")
    span = f"usually Rs {row.min_inr:,}" + ("" if row.min_inr == row.max_inr else f"–{row.max_inr:,}")
    return BudgetLine(
        category=category, description=f"{what} ({span} {per})".replace("  ", " ").replace(" )", ")"),
        unit_cost=unit_cost, qty=qty, total=unit_cost * qty,
        source=Source(name=row.source_note, url=row.source_url if row.source_url.startswith("http") else None),
        confidence=Confidence.estimated,
    )


def stay_cost(rows: list[CostRow], style: str, nights: int, rooms: int) -> BudgetLine:
    row = find(rows, "stay", style)
    unit = row.typical_inr if row else 0
    return _line("stay", f"{style.title()} stay, {plural(rooms, 'room')} × {plural(nights, 'night')}", row, unit,
                 nights * rooms)


def meal_cost(rows: list[CostRow], people: int, meals: int, style: str = "budget") -> BudgetLine:
    row = find(rows, "meal", style)
    unit = row.typical_inr if row else 0
    return _line("food", f"Meals, {plural(people, 'person')} × {plural(meals, 'meal')}", row, unit, people * meals)


def intercity_cost(rows: list[CostRow], mode: str, km: float, people: int, trips: int = 2) -> BudgetLine:
    """Round trip by default. Per-person modes scale by people; vehicle modes by cabs needed."""
    row = find(rows, "intercity", mode)
    if row is None:
        return _line("transport", mode.replace("_", " ").capitalize(), None, 0, trips)
    unit = round(row.typical_inr * km / 100)
    qty = math.ceil(people / VEHICLE_SEATS) * trips if row.unit == "vehicle_100km" else people * trips
    journey = "return trip" if trips == 2 else plural(trips, "trip")
    return _line("transport", f"{mode.replace('_', ' ').capitalize()}, {km:.0f} km each way, {journey}", row,
                 unit, qty)


def duration_factor(rows: list[CostRow], mode: str) -> float | None:
    row = find(rows, "duration_factor", mode)
    return row.typical_inr / 100 if row else None


def local_transport_cost(rows: list[CostRow], days: int, style: str = "budget") -> BudgetLine:
    row = find(rows, "local_transport", style)
    unit = row.typical_inr if row else 0
    return _line("local_transport", f"Local transport, {plural(days, 'day')}", row, unit, days)


def entry_fee(rows: list[CostRow], kind: str, people: int, place_name: str, place_id: str | None = None) -> BudgetLine:
    row = ((place_id and find(rows, "entry_fee", place_id)) or find(rows, "entry_fee", kind)
           or find(rows, "entry_fee", "default"))
    unit = row.typical_inr if row else 0
    return _line("activities", f"Entry: {place_name}", row, unit, people)
