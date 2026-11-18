"""Daily weather from Open-Meteo: forecast inside the 16-day horizon, 5-year climate normal beyond it."""
import logging
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from statistics import mean
from urllib.parse import urlencode

from app.core import cache
from app.schemas.place import Confidence, Source
from app.schemas.plan import DayWeather
from app.tools.http import get_json
from app.tools.routing import LatLon

log = logging.getLogger(__name__)
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_HORIZON_DAYS = 16  # Open-Meteo forecast_days maximum
# Open-Meteo counts its window from today's date in UTC, which can be a day behind India time near midnight,
# so the last forecast day we ask for is kept one day inside the window.
SAFETY_DAYS = 1
NORMAL_YEARS = 5
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"

# WMO weather interpretation codes, from the Open-Meteo forecast API docs.
WMO: dict[int, str] = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


def label(code: int | None) -> str:
    return WMO.get(code, "Unknown") if code is not None else "Unavailable"


def is_rainy(w: DayWeather) -> bool:
    text = w.label.lower()
    return any(k in text for k in ("rain", "drizzle", "thunder", "shower")) or (w.precip_prob or 0) >= 60


def _dates(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


async def _forecast(lat: float, lon: float, start: date, end: date) -> list[DayWeather]:
    params = {
        "latitude": round(lat, 4), "longitude": round(lon, 4), "timezone": "Asia/Kolkata",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "start_date": start.isoformat(), "end_date": end.isoformat(),
    }
    data = await cache.get_or_fetch("open_meteo", {"u": FORECAST_URL, **params},
                                    lambda: get_json("open_meteo", FORECAST_URL, params), timedelta(hours=3))
    d = data["daily"]
    src = Source(name=ATTRIBUTION, url=f"{FORECAST_URL}?{urlencode(params)}", retrieved_at=datetime.now(UTC))
    return [
        DayWeather(date=date.fromisoformat(t), kind="forecast", code=d["weather_code"][i],
                   label=label(d["weather_code"][i]), tmax_c=d["temperature_2m_max"][i],
                   tmin_c=d["temperature_2m_min"][i], precip_prob=d["precipitation_probability_max"][i],
                   source=src, confidence=Confidence.verified)
        for i, t in enumerate(d["time"])
    ]


def _shift_year(d: date, years_back: int) -> date:
    try:
        return d.replace(year=d.year - years_back)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year - years_back, day=28)


async def _climate_normal(lat: float, lon: float, start: date, end: date) -> list[DayWeather]:
    """Average the same calendar dates over the last NORMAL_YEARS years from the archive API."""
    days = _dates(start, end)
    per_day: dict[date, list[tuple]] = {d: [] for d in days}
    urls = []
    # Count back from the last full year so every archive date is in the past.
    first_back = max(1, start.year - date.today().year + 1)
    for back in range(first_back, first_back + NORMAL_YEARS):
        s, e = _shift_year(start, back), _shift_year(end, back)
        params = {"latitude": round(lat, 4), "longitude": round(lon, 4), "timezone": "Asia/Kolkata",
                  "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
                  "start_date": s.isoformat(), "end_date": e.isoformat()}
        urls.append(f"{ARCHIVE_URL}?{urlencode(params)}")
        data = await cache.get_or_fetch("open_meteo", {"u": ARCHIVE_URL, **params},
                                        lambda p=params: get_json("open_meteo", ARCHIVE_URL, p), timedelta(days=30))
        dd = data["daily"]
        for i, d in enumerate(days[: len(dd["time"])]):
            per_day[d].append((dd["weather_code"][i], dd["temperature_2m_max"][i],
                               dd["temperature_2m_min"][i], dd["precipitation_sum"][i]))
    src = Source(name=f"{ATTRIBUTION}; {NORMAL_YEARS}-year average, typical not a forecast",
                 url=urls[0] if urls else ARCHIVE_URL, retrieved_at=datetime.now(UTC))
    out = []
    for d in days:
        rows = [r for r in per_day[d] if r[0] is not None]
        if not rows:
            out.append(DayWeather(date=d, kind="climate_normal", label="Unavailable", source=src,
                                  confidence=Confidence.unavailable))
            continue
        code = Counter(r[0] for r in rows).most_common(1)[0][0]
        out.append(DayWeather(
            date=d, kind="climate_normal", code=code, label=label(code),
            tmax_c=round(mean(r[1] for r in rows if r[1] is not None), 1),
            tmin_c=round(mean(r[2] for r in rows if r[2] is not None), 1),
            precip_mm=round(mean(r[3] for r in rows if r[3] is not None), 1),
            source=src, confidence=Confidence.estimated,
        ))
    return out


async def daily_weather(p: LatLon, start: date, end: date, today: date | None = None) -> list[DayWeather]:
    today = today or date.today()
    horizon = today + timedelta(days=FORECAST_HORIZON_DAYS - 1 - SAFETY_DAYS)
    out: list[DayWeather] = []
    if start <= horizon:
        try:
            out += await _forecast(p.lat, p.lon, start, min(end, horizon))
        except Exception as e:  # noqa: BLE001 - a rejected forecast still leaves typical weather for those days
            log.warning("forecast failed, using climate normals instead: %s", str(e).splitlines()[0][:160])
            out += await _climate_normal(p.lat, p.lon, start, min(end, horizon))
    if end > horizon:
        out += await _climate_normal(p.lat, p.lon, max(start, horizon + timedelta(days=1)), end)
    return out
