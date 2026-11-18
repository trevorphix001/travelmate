"""Apply db/migrations/*.sql in order (alternative to pasting into the Supabase SQL editor).

Run: python -m scripts.migrate
"""
import asyncio
import logging
import sys
from pathlib import Path

from app.db.pool import close_pool, open_pool

log = logging.getLogger("migrate")


async def main() -> None:
    pool = await open_pool()
    if pool is None:
        sys.exit("DATABASE_URL is not set")
    for f in sorted(Path("db/migrations").glob("*.sql")):
        async with pool.acquire() as conn:
            await conn.execute(f.read_text(encoding="utf-8"))
        log.info("applied %s", f.name)
    await close_pool()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(main())
