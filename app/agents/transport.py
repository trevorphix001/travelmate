"""Transport node: no LLM. Prices only from the cost table; durations from OSRM x table factors."""
import logging

from app.agents.costs_data import load_cost_rows
from app.schemas.place import Confidence
from app.schemas.plan import TransportOption
from app.tools import costs
from app.tools.routing import route

log = logging.getLogger(__name__)
LOCAL_TRIP_KM = 10  # origin and destination effectively the same place


async def transport_node(state: dict) -> dict:
    req, a, b = state["request"], state["origin_geo"], state["dest_geo"]
    leg = await route(a, b)
    if leg.km < LOCAL_TRIP_KM:
        return {"intercity_options": [], "intercity_minutes": 0}
    rows, _tier = await load_cost_rows(b)
    options = []
    for mode in costs.items(rows, "intercity"):
        line = costs.intercity_cost(rows, mode, leg.km, req.travellers)
        factor = costs.duration_factor(rows, mode) or 1.0
        options.append(TransportOption(
            mode=mode, km=leg.km, minutes=round(leg.minutes * factor), total_inr=line.total,
            description=line.description, leg_source=leg.source, source=line.source,
            confidence=Confidence.estimated if leg.confidence == "verified" else leg.confidence,
        ))
    warnings = [] if options else [f"Bus, taxi and cab fares to {b.name} aren't available yet"]
    return {"intercity_options": sorted(options, key=lambda o: o.total_inr), "intercity_minutes": leg.minutes,
            "warnings": warnings}

