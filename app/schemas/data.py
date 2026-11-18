"""Rows of the seeded reference tables (data/*.csv -> Postgres)."""
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CostRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    city_tier: int = Field(ge=0, le=3)  # 0 = applies to every tier
    category: str
    item: str
    unit: str
    min_inr: int = Field(ge=0)
    typical_inr: int = Field(ge=0)
    max_inr: int = Field(ge=0)
    source_note: str
    source_url: str = Field(min_length=1)
    checked_on: date

    @model_validator(mode="after")
    def _range(self) -> "CostRow":
        if not self.min_inr <= self.typical_inr <= self.max_inr:
            raise ValueError("need min_inr <= typical_inr <= max_inr")
        return self

    @property
    def unverified(self) -> bool:
        """Starter rows have no web citation yet (their source_url points at docs/)."""
        return not self.source_url.startswith("http")


class City(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    state: str
    tier: int = Field(ge=1, le=3)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
