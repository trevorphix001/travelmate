"""Freeze every tool output the planners need, so experiments are reproducible and offline.

    python -m research.snapshot            # all 40 eval cases (resumable)

For each case it stores: the request, geocoded origin/destination, the deterministic candidate pool
(Overpass -> pre_rank, the same pool the Research agent sees), the OSRM travel-time matrix
[centre, *pool], the intercity road leg, and a 10-year daily precipitation series used to estimate the
climatological wet-day probability for any date (research/../app/planning/rain.py).
"""
import asyncio
import json
import logging
import sys
from datetime import date
from pathlib import Path

from app.agents.research import gather_places, max_pick, pre_rank
from app.tools.geocode import geocode
from app.tools.http import get_json
from app.tools.routing import matrix, route
from eval.run import load_cases, to_request

log = logging.getLogger("snapshot")
OUT = Path("research/data/snapshots")
ANCHOR = date(2026, 11, 19)  # fixed "today" so dates (and therefore weather) are reproducible
POOL = 24  # candidates kept per case: max_pick for a 5-day packed trip is 33, most cases need 7-23
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"


async def precip_series(lat: float, lon: float) -> dict[str, float]:
    params = {"latitude": round(lat, 3), "longitude": round(lon, 3), "timezone": "Asia/Kolkata",
              "daily": "precipitation_sum", "start_date": "2015-01-01", "end_date": "2024-12-31"}
    d = (await get_json("open_meteo", ARCHIVE, params))["daily"]
    return {t: v for t, v in zip(d["time"], d["precipitation_sum"], strict=True) if v is not None}


async def snap(case: dict, rain_cache: dict) -> dict:
    req = to_request(case, ANCHOR)
    origin = await geocode(req.origin)
    dest = await geocode(req.destination) or await geocode(req.destination, country=None)
    places, _ = await gather_places(dest, req)
    pool = pre_rank(places, req, dest.name, dest)[: max(POOL, max_pick(req))]
    m = await matrix([dest, *pool])
    leg = await route(origin, dest)
    key = f"{round(dest.lat, 1)},{round(dest.lon, 1)}"
    if key not in rain_cache:
        rain_cache[key] = await precip_series(dest.lat, dest.lon)
    return {"case": case, "request": req.model_dump(mode="json"), "origin": origin.model_dump(mode="json"),
            "dest": dest.model_dump(mode="json"), "pool": [p.model_dump(mode="json") for p in pool],
            "matrix": [[[c.km, c.minutes] for c in row] for row in m],
            "intercity": {"km": leg.km, "minutes": leg.minutes}, "rain_key": key, "precip": rain_cache[key]}


async def main(reverse: bool = False) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rain_path = OUT.parent / "precip_series.json"
    rain = json.loads(rain_path.read_text()) if rain_path.exists() else {}
    for case in (load_cases()[::-1] if reverse else load_cases()):
        out = OUT / f"{case['case_id']}.json"
        if out.exists():
            continue
        try:
            data = await snap(case, rain)
        except Exception as e:  # noqa: BLE001 - keep going; report the failure
            log.warning("%s failed: %s", case["case_id"], str(e)[:200])
            continue
        out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        rain_path.write_text(json.dumps(rain), encoding="utf-8")
        log.info("%s ok: %d places", case["case_id"], len(data["pool"]))
        await asyncio.sleep(1.0)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    asyncio.run(main(reverse="--reverse" in sys.argv))
