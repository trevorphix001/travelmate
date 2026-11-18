"""Validate data/cost_table.csv and data/cities.csv with Pydantic and upsert them into Postgres.

Refuses rows with an empty source_url. Run: python -m scripts.seed
"""
import asyncio
import csv
import logging
import sys
from pathlib import Path

from pydantic import ValidationError

from app.db.pool import close_pool, open_pool
from app.schemas.data import City, CostRow

log = logging.getLogger("seed")


def load(path: Path, model: type) -> list:
    rows, bad = [], 0
    with path.open(encoding="utf-8") as f:
        for i, raw in enumerate(csv.DictReader(f), start=2):
            try:
                rows.append(model(**raw))
            except ValidationError as e:
                bad += 1
                log.error("%s line %d rejected: %s", path, i, e.errors()[0]["msg"])
    if bad:
        sys.exit(f"{bad} invalid row(s) in {path}; fix them first")
    return rows


async def main() -> None:
    costs = load(Path("data/cost_table.csv"), CostRow)
    cities = load(Path("data/cities.csv"), City)
    unverified = sum(r.unverified for r in costs)
    pool = await open_pool()
    if pool is None:
        sys.exit("DATABASE_URL is not set")
    async with pool.acquire() as conn, conn.transaction():
        await conn.executemany(
            """insert into cost_table (version, city_tier, category, item, unit, min_inr, typical_inr, max_inr,
                                       source_note, source_url, checked_on)
               values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
               on conflict (version, city_tier, category, item) do update set unit=excluded.unit,
                 min_inr=excluded.min_inr, typical_inr=excluded.typical_inr, max_inr=excluded.max_inr,
                 source_note=excluded.source_note, source_url=excluded.source_url, checked_on=excluded.checked_on""",
            [(r.version, r.city_tier, r.category, r.item, r.unit, r.min_inr, r.typical_inr, r.max_inr,
              r.source_note, r.source_url, r.checked_on) for r in costs],
        )
        await conn.executemany(
            """insert into cities (name, state, tier, lat, lon) values ($1,$2,$3,$4,$5)
               on conflict (name) do update set state=excluded.state, tier=excluded.tier,
                 lat=excluded.lat, lon=excluded.lon""",
            [(c.name, c.state, c.tier, c.lat, c.lon) for c in cities],
        )
    await close_pool()
    log.info("upserted %d cost rows (%d preliminary, uncited) and %d cities", len(costs), unverified, len(cities))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(main())
