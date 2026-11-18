"""Road routing via OSRM (coordinates are lon,lat). Falls back to a labelled straight-line estimate."""
import logging
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import BaseModel

from app.core import cache
from app.core.config import get_settings
from app.schemas.place import Confidence, Source
from app.schemas.plan import Leg
from app.tools.geo import haversine_km
from app.tools.http import get_json

log = logging.getLogger(__name__)

ROAD_FACTOR = 1.4  # straight-line -> road distance, per PRD section 5
# No elevation data is available offline, so the fallback uses the PRD's conservative hill speed
# everywhere; overestimating travel time keeps days feasible rather than overfull.
FALLBACK_KMH = 30
ESTIMATE_SOURCE = Source(name="Straight-line estimate (haversine x 1.4, 30 km/h)")

# OSRM's public car profile assumes free-flowing roads (it gave 62-81 km/h for Guwahati-Shillong and 71 km/h
# inside Shillong town). Real Indian hill and town driving is far slower: the 97 km Guwahati-Shillong road
# takes roughly 2.5 h by car and 3 h by bus. Durations are therefore capped at these average speeds; the
# distance and route line still come from OSRM. Planning assumptions, like the visit durations.
TOWN_LEG_KM = 15
MAX_KMH_TOWN = 25  # short legs: traffic, junctions, parking
MAX_KMH_ROAD = 40  # longer legs: highways mixed with hill sections
SPEED_NOTE = "travel time adjusted for local road conditions"


def realistic_minutes(km: float, minutes: int) -> tuple[int, bool]:
    """(minutes, adjusted): never faster than the realistic average speed for that distance."""
    kmh = MAX_KMH_TOWN if km <= TOWN_LEG_KM else MAX_KMH_ROAD
    floor = round(km / kmh * 60)
    return (floor, True) if floor > minutes else (minutes, False)


class LatLon(Protocol):
    lat: float
    lon: float


class LegLite(BaseModel):
    km: float
    minutes: int
    estimated: bool = False


def _coords(points: list[LatLon]) -> str:
    return ";".join(f"{p.lon:.5f},{p.lat:.5f}" for p in points)


def estimate_leg(a: LatLon, b: LatLon) -> Leg:
    km = haversine_km(a.lat, a.lon, b.lat, b.lon) * ROAD_FACTOR
    return Leg(km=round(km, 1), minutes=round(km / FALLBACK_KMH * 60),
               geometry=[[a.lon, a.lat], [b.lon, b.lat]], source=ESTIMATE_SOURCE,
               confidence=Confidence.estimated)


def parse_route(data: dict, url: str) -> list[Leg]:
    src = Source(name="OSRM (OpenStreetMap routing)", url=url, retrieved_at=datetime.now(UTC))
    legs = []
    for leg in data["routes"][0]["legs"]:
        geom: list[list[float]] = []
        for step in leg.get("steps", []):
            pts = step["geometry"]["coordinates"]
            geom.extend(pts if not geom else pts[1:])
        km = round(leg["distance"] / 1000, 1)
        minutes, adjusted = realistic_minutes(km, round(leg["duration"] / 60))
        leg_src = src.model_copy(update={"name": f"{src.name}; {SPEED_NOTE}"}) if adjusted else src
        legs.append(Leg(km=km, minutes=minutes, geometry=geom or None, source=leg_src,
                        confidence=Confidence.estimated if adjusted else Confidence.verified))
    return legs


async def route_multi(points: list[LatLon]) -> list[Leg]:
    """One OSRM call through all points; returns len(points)-1 legs with per-leg geometry."""
    if len(points) < 2:
        return []
    url = f"{get_settings().osrm_url}/route/v1/driving/{_coords(points)}"
    params = {"overview": "false", "steps": "true", "geometries": "geojson"}
    try:
        data = await cache.get_or_fetch("osrm", {"url": url, **params},
                                        lambda: get_json("osrm", url, params), timedelta(days=30))
        if data.get("code") != "Ok":
            raise ValueError(f"OSRM code {data.get('code')}")
        return parse_route(data, url)
    except Exception as e:  # noqa: BLE001 - fallback is labelled 'estimated'
        log.warning("OSRM route failed, using straight-line estimate: %s", e)
        return [estimate_leg(a, b) for a, b in zip(points, points[1:], strict=False)]


async def route(a: LatLon, b: LatLon) -> Leg:
    return (await route_multi([a, b]))[0]


def parse_table(data: dict, points: list[LatLon]) -> list[list[LegLite]]:
    out = []
    for i, row in enumerate(data["durations"]):
        cells = []
        for j, dur in enumerate(row):
            dist = data["distances"][i][j]
            if dur is None or dist is None:
                e = estimate_leg(points[i], points[j])
                cells.append(LegLite(km=e.km, minutes=e.minutes, estimated=True))
            else:
                km = round(dist / 1000, 1)
                cells.append(LegLite(km=km, minutes=realistic_minutes(km, round(dur / 60))[0]))
        out.append(cells)
    return out


async def matrix(points: list[LatLon]) -> list[list[LegLite]]:
    url = f"{get_settings().osrm_url}/table/v1/driving/{_coords(points)}"
    params = {"annotations": "duration,distance"}
    try:
        data = await cache.get_or_fetch("osrm", {"url": url, **params},
                                        lambda: get_json("osrm", url, params), timedelta(days=30))
        if data.get("code") != "Ok":
            raise ValueError(f"OSRM code {data.get('code')}")
        return parse_table(data, points)
    except Exception as e:  # noqa: BLE001
        log.warning("OSRM table failed, using straight-line estimates: %s", e)
        return [[LegLite(km=(lg := estimate_leg(a, b)).km, minutes=lg.minutes, estimated=True) for b in points]
                for a in points]
