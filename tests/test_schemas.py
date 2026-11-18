from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.schemas.place import Confidence, Place, Source
from app.schemas.plan import BudgetLine, DayWeather
from app.schemas.trip import Interest, Pace, TripRequest

T = date.today() + timedelta(days=10)


def req(**kw):
    base = dict(origin="A", destination="B", start_date=T, end_date=T + timedelta(days=2),
                travellers=2, budget_inr=8000, interests=["nature"])
    base["origin"], base["destination"] = "Origin", "Destination"
    base.update(kw)
    return TripRequest(**base)


def test_valid_request():
    r = req()
    assert r.num_days == 3 and r.pace is Pace.normal and r.interests == [Interest.nature]


@pytest.mark.parametrize("kw", [
    {"end_date": T - timedelta(days=1)},
    {"end_date": T + timedelta(days=7)},  # 8 days
    {"start_date": date.today() - timedelta(days=1), "end_date": date.today()},
    {"travellers": 0}, {"travellers": 11},
    {"budget_inr": 0},
    {"interests": ["skydiving"]},
    {"pace": "frantic"},
    {"unexpected": 1},
])
def test_invalid_request(kw):
    with pytest.raises(ValidationError):
        req(**kw)


def test_seven_day_trip_allowed():
    assert req(end_date=T + timedelta(days=6)).num_days == 7


def test_factual_models_require_source():
    with pytest.raises(ValidationError):
        Place(id="osm:node/1", name="x", kind="k", lat=0, lon=0, confidence="verified")
    with pytest.raises(ValidationError):
        BudgetLine(category="stay", description="d", unit_cost=1, qty=1, total=1, confidence="estimated")
    src = Source(name="Open-Meteo")
    with pytest.raises(ValidationError):
        DayWeather(date=T, kind="guess", label="x", source=src, confidence=Confidence.verified)
    DayWeather(date=T, kind="forecast", label="Clear", source=src, confidence="verified")
