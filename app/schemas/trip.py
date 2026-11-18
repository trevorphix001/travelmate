from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

MAX_TRIP_DAYS = 7


class Interest(StrEnum):
    nature = "nature"
    waterfalls = "waterfalls"
    heritage = "heritage"
    food = "food"
    adventure = "adventure"
    religious = "religious"
    shopping = "shopping"
    nightlife = "nightlife"


class Pace(StrEnum):
    relaxed = "relaxed"
    normal = "normal"
    packed = "packed"


class TripRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: str = Field(min_length=2, max_length=80)
    destination: str = Field(min_length=2, max_length=80)
    start_date: date
    end_date: date
    travellers: int = Field(ge=1, le=10)
    budget_inr: int = Field(gt=0, le=10_000_000)
    interests: list[Interest] = Field(default_factory=list, max_length=8)
    pace: Pace = Pace.normal

    @model_validator(mode="after")
    def _dates(self, info: ValidationInfo) -> "TripRequest":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.num_days > MAX_TRIP_DAYS:
            raise ValueError(f"trip length must be at most {MAX_TRIP_DAYS} days")
        allow_past = bool(info.context and info.context.get("allow_past"))
        if not allow_past and self.start_date < date.today():
            raise ValueError("start_date must not be in the past")
        return self

    @property
    def num_days(self) -> int:
        return (self.end_date - self.start_date).days + 1
