"""Geo resolver: origin and destination -> GeoPoint. No match is an error, never a guessed coordinate."""
from app.tools.geocode import geocode


async def geo_node(state: dict) -> dict:
    req = state["request"]
    origin = await geocode(req.origin)
    dest = await geocode(req.destination) or await geocode(req.destination, country=None)
    errors = [f"Could not find '{name}' on OpenStreetMap" for name, g in
              ((req.origin, origin), (req.destination, dest)) if g is None]
    if errors:
        return {"errors": errors}
    warnings = []
    if dest.tier is None:
        warnings.append(f"Prices for {dest.name} are based on similar-sized cities")
    return {"origin_geo": origin, "dest_geo": dest, "warnings": warnings}
