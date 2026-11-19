import json
import logging

import asyncpg

from app.core.config import get_settings

log = logging.getLogger(__name__)
_pool: asyncpg.Pool | None = None


async def _init_conn(conn: asyncpg.Connection) -> None:
    for typ in ("jsonb", "json"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def open_pool() -> asyncpg.Pool | None:
    """Open the global pool if DATABASE_URL is set. Returns None (and logs) otherwise."""
    global _pool
    st = get_settings()
    dsn = st.database_url
    if st.stateless:
        log.info("stateless mode: no database; reference data from data/*.csv, cache in memory, plans in the browser")
        return None
    if _pool is None and dsn and dsn.get_secret_value():
        # statement_cache_size=0 keeps us compatible with Supabase's pgbouncer pooler.
        _pool = await asyncpg.create_pool(
            dsn.get_secret_value(), min_size=1, max_size=5, statement_cache_size=0,
            init=_init_conn, command_timeout=10,
        )
        log.info("database pool opened")
    elif _pool is None:
        log.warning("DATABASE_URL not set: persistence, cost table and cities are unavailable")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool | None:
    return _pool
