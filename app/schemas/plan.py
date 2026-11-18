from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.place import Confidence, GeoPoint, Place, Source
from app.schemas.trip import TripRequest

_strict = ConfigDict(extra="forbid")


class Leg(BaseModel):
    model_config = _strict
    km: float
    minutes: int
    geometry: list[list[float]] | None = None  # GeoJSON [lon, lat] pairs
    source: Source
    confidence: Confidence


class Stop(BaseModel):
    model_config = _strict
    place_id: str
    arrive: str  # "HH:MM"
    depart: str
    leg_from_prev: Leg | None = None
    hours_note: str | None = None


class Meal(BaseModel):
    model_config = _strict
    kind: Literal["lunch"] = "lunch"
    start: str
    end: str


class DayPlan(BaseModel):
    model_config = _strict
    date: date
    window_start: str
    window_end: str
    stops: list[Stop] = []
    meals: list[Meal] = []
    return_leg: Leg | None = None  # last stop back to the stay area
    notes: list[str] = []
    end_time: str | None = None  # when the day actually finishes, computed in Python


class DayWeather(BaseModel):
    model_config = _strict
    date: date
    kind: Literal["forecast", "climate_normal"]
    code: int | None = None
    label: str
    tmax_c: float | None = None
    tmin_c: float | None = None
    precip_prob: int | None = None  # percent; forecast only
    precip_mm: float | None = None  # climate normal only
    source: Source
    confidence: Confidence


class TransportOption(BaseModel):
    model_config = _strict
    mode: str
    km: float
    minutes: int
    total_inr: int
    description: str
    leg_source: Source
    source: Source
    confidence: Confidence


class BudgetLine(BaseModel):
    model_config = _strict
    category: str
    description: str
    unit_cost: int
    qty: int
    total: int
    source: Source
    confidence: Confidence


class BudgetBreakdown(BaseModel):
    model_config = _strict
    lines: list[BudgetLine]
    grand_total: int
    per_person: int
    budget_inr: int
    within_budget: bool
    gap_inr: int = 0  # how far over budget, 0 when within
    stay_style: str
    transport_mode: str | None
    include_paid_activities: bool
    suggestions: list[str] = []
    cost_table_version: str


class NodeMetric(BaseModel):
    model_config = _strict
    node: str
    provider: str | None = None
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    llm_calls: int = 0
    ms: int = 0
    ok: bool = True
    fallback_used: bool = False
    cached: bool = False


class TripPlan(BaseModel):
    model_config = _strict
    id: str | None = None
    title: str
    request: TripRequest
    origin_geo: GeoPoint
    dest_geo: GeoPoint
    days: list[DayPlan]
    places: list[Place]  # every candidate the planner could use; stops reference these IDs
    budget: BudgetBreakdown
    weather: list[DayWeather]
    intercity_options: list[TransportOption]
    warnings: list[str] = []
    sources: list[Source] = []
    trace: list[NodeMetric] = []
    created_at: datetime | None = None


class PlanDiff(BaseModel):
    model_config = _strict
    op: str
    changed_dates: list[date] = []
    added: list[str] = []
    removed: list[str] = []
    old_total: int
    new_total: int


class PlanSummary(BaseModel):
    model_config = _strict
    id: str
    title: str
    start_date: date
    end_date: date
    total: int
    created_at: datetime


class Clarification(BaseModel):
    model_config = _strict
    needs_clarification: Literal[True] = True
    question: str
    missing: list[str] = Field(default_factory=list)
