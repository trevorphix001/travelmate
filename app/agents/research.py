"""Research: Overpass places -> deterministic pre-rank -> LLM ranks by ID -> unknown IDs dropped."""
import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from app.core.errors import AppError
from app.db import repo
from app.llm import prompts
from app.llm.client import complete_json
from app.schemas.place import Confidence, GeoPoint, Place, Source
from app.schemas.plan import NodeMetric
from app.schemas.trip import Pace, TripRequest
from app.tools import geoapify_places, wiki, wiki_places
from app.tools.attractions import find_places
from app.tools.geo import haversine_km

log = logging.getLogger(__name__)
STALE_AFTER = timedelta(days=7)  # Overpass cache TTL: anything older was served by the stale fallback
STOPS_PER_DAY = {Pace.relaxed: 3, Pace.normal: 4, Pace.packed: 6}
POOL_SIZE = 40  # candidates shown to the LLM (compact) and kept for edits
MAX_WIKI = 15
MIN_FOR_LLM = 4
TOURIST_KINDS = {"waterfall", "peak", "viewpoint", "museum", "attraction", "park", "nature_reserve", "lake",
                 "cave_entrance", "fort", "castle", "monument", "palace", "ruins", "archaeological_site"}


class RankOut(BaseModel):
    ranked_ids: list[str]


def max_pick(req: TripRequest) -> int:
    return req.num_days * STOPS_PER_DAY[req.pace] + 3


KM_PER_POINT = 15.0  # every 15 km from the stay costs one point: far places must be clearly better matches


def score(p: Place, req: TripRequest, km: float = 0.0) -> float:
    """Deterministic score: interest match first, then notability (wiki), tourist kinds, then distance."""
    wanted = {i.value for i in req.interests}
    s = 3.0 * len(wanted & set(p.interests)) - km / KM_PER_POINT
    s += 2.0 if p.wiki_tag else 0.0
    s += 1.0 if p.kind in TOURIST_KINDS else 0.0
    if p.kind in {"restaurant", "cafe"} and "food" not in wanted:
        s -= 3.0
    return s


def km_from(p: Place, dest: GeoPoint | None) -> float:
    return haversine_km(dest.lat, dest.lon, p.lat, p.lon) if dest else 0.0


def pre_rank(places: list[Place], req: TripRequest, dest_name: str, dest: GeoPoint | None = None) -> list[Place]:
    pool = [p for p in places if p.name.strip().lower() != dest_name.strip().lower()]
    return sorted(pool, key=lambda p: (-score(p, req, km_from(p, dest)), p.name))[:POOL_SIZE]


def compact(p: Place, km: float | None = None) -> dict:
    d = {"id": p.id, "name": p.name[:60], "kind": p.kind, "interests": p.interests, "has_wiki": bool(p.wiki_tag)}
    if km is not None:
        d["km"] = round(km)  # straight-line distance from the stay, computed in Python
    if p.summary:
        d["note"] = p.summary[:80]
    return d


async def _add_summaries(pool: list[Place]) -> list[Place]:
    targets = [p for p in pool if p.wiki_tag][:MAX_WIKI]
    results = await asyncio.gather(*(wiki.summary(p.wiki_tag) for p in targets))
    by_id = {p.id: r for p, r in zip(targets, results, strict=True) if r}
    out = []
    for p in pool:
        r = by_id.get(p.id)
        out.append(p.model_copy(update={"summary": r["extract"], "wiki_url": r["url"] or p.wiki_url}) if r else p)
    return out


def merge_ranking(ranked_ids: list[str], pool: list[Place], need: int) -> tuple[list[Place], list[str]]:
    """Keep only known IDs (in LLM order), then top up from the deterministic order."""
    by_id = {p.id: p for p in pool}
    unknown = [i for i in ranked_ids if i not in by_id]
    chosen = list(dict.fromkeys(i for i in ranked_ids if i in by_id))
    for p in pool:
        if len(chosen) >= need:
            break
        if p.id not in chosen:
            chosen.append(p.id)
    return [by_id[i] for i in chosen[:need]], unknown


async def gather_places(dest: GeoPoint, req: TripRequest) -> tuple[list[Place], list[str]]:
    """Overpass (live, else its stale cache), then Geoapify (same OSM data, if a key is set), then Wikipedia."""
    try:
        places = await find_places(dest, list(req.interests), min_places=max_pick(req))
    except AppError as e:
        if e.type != "overpass_unavailable":
            raise
        if geoapify_places.enabled():
            try:
                geo_found = await geoapify_places.find_places(dest, list(req.interests), min_places=max_pick(req))
            except AppError as g:
                log.warning("Geoapify fallback failed: %s", g.detail)
                geo_found = []
            if len(geo_found) >= MIN_FOR_LLM:
                return geo_found, []  # same OpenStreetMap data from another server: nothing for the traveller to know
        log.warning("Overpass unavailable with no cache; falling back to Wikipedia GeoSearch")
        try:
            near = await repo.nearby_cities(dest.lat, dest.lon, max_km=60)
            centres = [GeoPoint(name=c.name, lat=c.lat, lon=c.lon, source=Source(name="TravelMate cities table"),
                                confidence=Confidence.verified) for c in near]
            wiki_found = await wiki_places.nearby_places(dest, centres, min_places=max_pick(req))
        except Exception as w:  # noqa: BLE001 - report both sources failing in one clear error
            hint = (" Wikipedia rejected the request: set APP_USER_AGENT in .env to include a real contact "
                    "(email or project URL)." if "403" in str(w) else " The Wikipedia fallback also failed.")
            raise AppError("attractions_unavailable", "No attractions source is responding",
                           "Public OpenStreetMap (Overpass) servers are busy." + hint, 503) from w
        return wiki_found, [
            "Fewer attractions are available right now; opening hours and entry fees may be missing"]
    oldest = min((p.source.retrieved_at for p in places if p.source.retrieved_at), default=None)
    if oldest and datetime.now(UTC) - oldest > STALE_AFTER:
        return places, [f"Attraction details were last updated on {oldest:%d %b %Y}"]
    return places, []


async def research_node(state: dict) -> dict:
    req: TripRequest = state["request"]
    dest = state["dest_geo"]
    places, warnings = await gather_places(dest, req)
    if not places:
        raise AppError("no_places", "No attractions found", f"No named attractions are mapped near {dest.name}")
    pool = await _add_summaries(pre_rank(places, req, dest.name, dest))
    need = max_pick(req)
    trace: list[NodeMetric] = []
    if len(pool) < MIN_FOR_LLM:
        warnings.append(f"Only a few attractions are listed near {dest.name}")
        return {"places": pool, "candidates": pool, "warnings": warnings}
    cands = [compact(p, km_from(p, dest)) for p in pool]
    user = json.dumps({"interests": [i.value for i in req.interests], "pace": req.pace.value, "days": req.num_days,
                       "max_pick": need, "candidates": cands}, ensure_ascii=False)
    ranked_ids: list[str] = []
    try:
        out, metric = await complete_json("planner", RankOut, prompts.RESEARCH_RANK_SYSTEM, user, max_tokens=900,
                                          temperature=0, node="research")
        ranked_ids = out.ranked_ids
        trace.append(metric)
    except AppError as e:
        log.info("attraction ranking used the deterministic fallback")
        log.warning("research LLM failed: %s", e)
    candidates, unknown = merge_ranking(ranked_ids, pool, need)
    if unknown:
        log.warning("dropped %d unknown IDs from LLM ranking: %s", len(unknown), unknown[:5])
    return {"places": pool, "candidates": candidates, "warnings": warnings, "trace": trace}
