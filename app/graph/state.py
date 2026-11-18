import operator
from datetime import date
from typing import Annotated, TypedDict

from app.schemas.place import GeoPoint, Place
from app.schemas.plan import BudgetBreakdown, DayPlan, DayWeather, NodeMetric, TransportOption, TripPlan
from app.schemas.trip import TripRequest


class Downgrade(TypedDict):
    stay_style: str  # "mid" -> "budget"
    transport_mode: str | None  # None = cheapest option within 8 h
    include_paid: bool  # False once some paid activities were cut


class TripState(TypedDict, total=False):
    raw_query: str
    request_input: dict  # structured form input; skips the Supervisor LLM call
    today: date
    request: TripRequest  # validated Pydantic model
    question: str  # clarification question; ends the run
    origin_geo: GeoPoint
    dest_geo: GeoPoint
    places: list[Place]  # deterministic pre-ranked pool from Overpass (kept for edits)
    candidates: list[Place]  # LLM-ranked subset, never from the LLM itself
    weather: list[DayWeather]
    intercity_options: list[TransportOption]
    intercity_minutes: int  # road time by car; used for day windows even when no fares are known
    days: list[DayPlan]  # place IDs + order + times
    budget: BudgetBreakdown
    within_budget: bool
    downgrade: Downgrade
    budget_cap: str  # note for the Itinerary agent after a downgrade
    retries: int
    budget_loop: bool  # True when the Budget node applied a downgrade and wants another pass
    replan: bool  # True when that downgrade changes which places are allowed (paid activities cut)
    excluded_ids: list[str]  # places the budget loop removed (paid stops cut + unused paid places)
    plan: TripPlan
    warnings: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]
    trace: Annotated[list[NodeMetric], operator.add]  # tokens, ms, provider
