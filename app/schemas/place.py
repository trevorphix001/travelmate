from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Confidence(StrEnum):
    verified = "verified"
    estimated = "estimated"
    unavailable = "unavailable"


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    url: str | None = None
    retrieved_at: datetime | None = None


class GeoPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    lat: float
    lon: float
    bbox: list[float] | None = None  # [south, north, west, east] as Nominatim returns it
    tier: int | None = None  # city tier from the cities table, when known
    source: Source
    confidence: "Confidence"


class Place(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str  # e.g. "osm:node/123"
    name: str
    kind: str  # matched OSM tag value, e.g. "waterfall"
    interests: list[str] = []  # Interest values this place matched
    lat: float
    lon: float
    opening_hours: str | None = None
    wiki_url: str | None = None
    wiki_tag: str | None = None  # raw wikipedia/wikidata tag, used for summary lookup
    summary: str | None = None
    fee: bool | None = None  # from OSM fee=yes/no when tagged
    source: Source
    confidence: Confidence
