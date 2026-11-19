"""All SQL lives here. Owner checks are enforced in every user-scoped query (in addition to RLS)."""
import csv
import logging
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import asyncpg

from app.core.errors import AppError
from app.db.pool import get_pool
from app.schemas.data import City, CostRow
from app.schemas.plan import NodeMetric, PlanDiff, PlanSummary, TripPlan
from app.tools.geo import haversine_km

log = logging.getLogger(__name__)
DATA = Path(__file__).resolve().parents[2] / "data"


@lru_cache(maxsize=1)
def _csv_costs() -> list[CostRow]:
    """Stateless mode: the curated cost table ships with the repo (data/cost_table.csv)."""
    with (DATA / "cost_table.csv").open(encoding="utf-8") as f:
        return [CostRow(**r) for r in csv.DictReader(f)]


@lru_cache(maxsize=1)
def _csv_cities() -> list[City]:
    with (DATA / "cities.csv").open(encoding="utf-8") as f:
        return [City(**r) for r in csv.DictReader(f)]


def _require_pool() -> asyncpg.Pool:
    pool = get_pool()
    if pool is None:
        raise AppError("db_unavailable", "Database not configured", "Set DATABASE_URL in .env", 503)
    return pool


def plan_from_row(data: dict, plan_id: str, created_at: datetime) -> TripPlan:
    data = dict(data)
    data["id"], data["created_at"] = plan_id, created_at
    # Saved plans may have past dates: skip the "start_date not in the past" check on load.
    return TripPlan.model_validate(data, context={"allow_past": True})


# ---- plans ---------------------------------------------------------------
async def save_plan(user_id: str, plan: TripPlan) -> str:
    row = await _require_pool().fetchrow(
        "insert into plans (user_id, title, request, plan) values ($1, $2, $3, $4) returning id",
        user_id, plan.title, plan.request.model_dump(mode="json"),
        plan.model_dump(mode="json", exclude={"id", "created_at"}),
    )
    return str(row["id"])


async def update_plan(plan_id: str, user_id: str, plan: TripPlan) -> None:
    await _require_pool().execute(
        "update plans set title=$3, request=$4, plan=$5 where id=$1 and user_id=$2",
        plan_id, user_id, plan.title, plan.request.model_dump(mode="json"),
        plan.model_dump(mode="json", exclude={"id", "created_at"}),
    )


async def get_plan(plan_id: str, user_id: str) -> TripPlan | None:
    try:
        row = await _require_pool().fetchrow(
            "select id, plan, created_at from plans where id=$1::uuid and user_id=$2::uuid",
            plan_id, user_id,
        )
    except asyncpg.DataError:  # malformed uuid
        return None
    return plan_from_row(row["plan"], str(row["id"]), row["created_at"]) if row else None


async def list_plans(user_id: str) -> list[PlanSummary]:
    rows = await _require_pool().fetch(
        """select id, title, request->>'start_date' as s, request->>'end_date' as e,
                  (plan->'budget'->>'grand_total')::int as total, created_at
           from plans where user_id=$1::uuid order by created_at desc limit 100""",
        user_id,
    )
    return [PlanSummary(id=str(r["id"]), title=r["title"], start_date=r["s"], end_date=r["e"],
                        total=r["total"] or 0, created_at=r["created_at"]) for r in rows]


async def delete_plan(plan_id: str, user_id: str) -> bool:
    try:
        res = await _require_pool().execute(
            "delete from plans where id=$1::uuid and user_id=$2::uuid", plan_id, user_id
        )
    except asyncpg.DataError:
        return False
    return res.endswith(" 1")


async def add_edit(plan_id: str, instruction: str, diff: PlanDiff) -> None:
    await _require_pool().execute(
        "insert into plan_edits (plan_id, instruction, diff) values ($1::uuid, $2, $3)",
        plan_id, instruction, diff.model_dump(mode="json"),
    )


# ---- reference data (degrades to empty when no DB) -------------------------
async def get_cost_rows(tier: int, version: str) -> list[CostRow]:
    pool = get_pool()
    if pool is None:
        return [r for r in _csv_costs() if r.version == version and r.city_tier in (0, tier)]
    rows = await pool.fetch(
        "select * from cost_table where version=$1 and city_tier in (0, $2)", version, tier
    )
    return [CostRow(**dict(r)) for r in rows]


async def get_city(name: str) -> City | None:
    pool = get_pool()
    if pool is None:
        return next((c for c in _csv_cities() if c.name.lower() == name.strip().lower()), None)
    r = await pool.fetchrow("select * from cities where lower(name)=lower($1)", name.strip())
    return City(**dict(r)) if r else None


async def nearby_cities(lat: float, lon: float, max_km: float) -> list[City]:
    """Curated cities within max_km (nearest first). The table is small, so distance is computed in Python."""
    pool = get_pool()
    cities = _csv_cities() if pool is None else [City(**dict(r)) for r in await pool.fetch("select * from cities")]
    near = [(haversine_km(lat, lon, c.lat, c.lon), c) for c in cities]
    return [c for d, c in sorted(near, key=lambda x: x[0]) if 1 < d <= max_km]


async def search_cities(prefix: str, limit: int = 8) -> list[City]:
    pool = get_pool()
    if not prefix.strip():
        return []
    if pool is None:
        q = prefix.strip().lower()
        return sorted((c for c in _csv_cities() if c.name.lower().startswith(q)), key=lambda c: c.name)[:limit]
    rows = await pool.fetch(
        "select * from cities where lower(name) like lower($1) || '%' order by name limit $2",
        prefix.strip(), limit,
    )
    return [City(**dict(r)) for r in rows]


# ---- metrics and cache -----------------------------------------------------
async def insert_metrics(run_id: str, metrics: list[NodeMetric]) -> None:
    pool = get_pool()
    if pool is None or not metrics:
        return
    await pool.executemany(
        """insert into node_metrics (run_id, node, provider, model, prompt_tokens, completion_tokens, ms, ok)
           values ($1,$2,$3,$4,$5,$6,$7,$8)""",
        [(run_id, m.node, m.provider, m.model, m.prompt_tokens, m.completion_tokens, m.ms, m.ok)
         for m in metrics],
    )


async def cache_get(key: str, allow_expired: bool = False) -> Any | None:
    pool = get_pool()
    if pool is None:
        return None
    if allow_expired:  # stale fallback when the live service is down
        return await pool.fetchval("select response from api_cache where key=$1", key)
    return await pool.fetchval("select response from api_cache where key=$1 and expires_at > now()", key)


async def cache_put(key: str, provider: str, response: Any, expires_at: datetime) -> None:
    pool = get_pool()
    if pool is None:
        return
    await pool.execute(
        """insert into api_cache (key, provider, response, expires_at) values ($1,$2,$3,$4)
           on conflict (key) do update set response=excluded.response, expires_at=excluded.expires_at""",
        key, provider, response, expires_at,
    )


async def ping() -> bool:
    pool = get_pool()
    if pool is None:
        return False
    try:
        return await pool.fetchval("select 1") == 1
    except Exception:  # noqa: BLE001
        log.exception("db ping failed")
        return False
