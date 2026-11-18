"""Weather node: wraps daily_weather; a failure becomes a warning, not an error."""
import logging

from app.tools.weather import daily_weather

log = logging.getLogger(__name__)


async def weather_node(state: dict) -> dict:
    req = state["request"]
    try:
        return {"weather": await daily_weather(state["dest_geo"], req.start_date, req.end_date)}
    except Exception as e:  # noqa: BLE001 - weather is optional for a plan
        log.warning("weather failed: %s", e)
        return {"weather": [], "warnings": ["Weather unavailable for these dates"]}
