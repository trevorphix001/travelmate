"""Turn hand-collected price samples into cited cost_table rows.

Each line of data/cost_samples.csv is ONE observed price with the page it came from. Rows with the same
(city_tier, category, item, unit) become one cost_table row: min = lowest sample, typical = median,
max = highest. Matching rows in data/cost_table.csv (e.g. the preliminary starter rows) are replaced;
all other rows are kept. Nothing is invented: every number is a sample someone recorded.

  python -m scripts.cost_research                  # preview what would change
  python -m scripts.cost_research --write          # update data/cost_table.csv
  python -m scripts.cost_research --check-urls     # also confirm every source_url loads
Then load it: python -m scripts.seed
"""
import argparse
import asyncio
import csv
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from statistics import median

from pydantic import BaseModel, Field, HttpUrl

from app.core import cache
from app.schemas.data import CostRow
from app.tools.http import request_once

log = logging.getLogger("cost_research")
SAMPLES = Path("data/cost_samples.csv")
TABLE = Path("data/cost_table.csv")
TABLE_FIELDS = list(CostRow.model_fields)


class Sample(BaseModel):
    city_tier: int = Field(ge=0, le=3)
    category: str = Field(min_length=1)
    item: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    price_inr: int = Field(ge=0)
    source_url: HttpUrl  # a real web page is required for every observation
    checked_on: date
    note: str = ""


def aggregate(samples: list[Sample], version: str) -> list[CostRow]:
    groups: dict[tuple, list[Sample]] = {}
    for s in samples:
        groups.setdefault((s.city_tier, s.category, s.item, s.unit), []).append(s)
    rows = []
    for (tier, cat, item, unit), ss in sorted(groups.items()):
        prices = sorted(s.price_inr for s in ss)
        notes = "; ".join(dict.fromkeys(s.note for s in ss if s.note))[:200]
        rows.append(CostRow(
            version=version, city_tier=tier, category=cat, item=item, unit=unit,
            min_inr=prices[0], typical_inr=round(median(prices)), max_inr=prices[-1],
            source_note=f"{len(ss)} sample(s)" + (f": {notes}" if notes else "")
                        + (f" (+{len(ss) - 1} more URLs in data/cost_samples.csv)" if len(ss) > 1 else ""),
            source_url=str(ss[0].source_url), checked_on=max(s.checked_on for s in ss),
        ))
    return rows


def merge(existing: list[CostRow], new: list[CostRow]) -> list[CostRow]:
    """New rows replace existing rows with the same key; everything else is kept."""
    key = lambda r: (r.version, r.city_tier, r.category, r.item)  # noqa: E731
    replaced = {key(r) for r in new}
    return [r for r in existing if key(r) not in replaced] + new


def read_csv(path: Path, model: type[BaseModel]) -> list:
    rows, errors = [], []
    with path.open(encoding="utf-8") as f:
        for n, raw in enumerate(csv.DictReader(f), start=2):
            try:
                rows.append(model(**{k: v for k, v in raw.items() if k}))
            except Exception as e:  # noqa: BLE001 - report every bad line, then stop
                errors.append(f"{path} line {n}: {str(e).splitlines()[0]} -> {str(e).splitlines()[-1].strip()}")
    if errors:
        sys.exit("\n".join(errors))
    return rows


async def check_urls(urls: list[str]) -> dict[str, str]:
    async def status(url: str) -> dict:
        try:
            await request_once("source_check", "GET", url)
            return {"ok": True, "detail": "200"}
        except Exception as e:  # noqa: BLE001 - JSON-decode errors still mean the page loaded
            text = str(e)
            ok = "Expecting value" in text or "JSON" in text
            return {"ok": ok, "detail": "loaded" if ok else text.splitlines()[0][:80]}

    out = {}
    for u in urls:
        res = await cache.get_or_fetch("source_check", {"url": u}, lambda u=u: status(u), timedelta(days=1))
        out[u] = "ok" if res["ok"] else f"FAILED ({res['detail']})"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write data/cost_table.csv")
    ap.add_argument("--check-urls", action="store_true", help="GET every source_url once")
    ap.add_argument("--version", default="v1", help="cost table version to write (set COST_TABLE_VERSION to match)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    samples = read_csv(SAMPLES, Sample)
    if not samples:
        sys.exit(f"{SAMPLES} has no samples yet: add one line per observed price (see docs/DATA_SOURCES.md)")
    new = aggregate(samples, a.version)
    existing = read_csv(TABLE, CostRow)
    merged = merge(existing, new)
    for r in new:
        log.info("%-15s %-22s tier %d  %6d / %6d / %6d  %s", r.category, r.item, r.city_tier, r.min_inr,
                 r.typical_inr, r.max_inr, r.source_note[:60])
    left = sum(r.unverified for r in merged)
    log.info("%d sampled rows; %d rows total; %d preliminary (uncited) rows still to research",
             len(new), len(merged), left)

    if a.check_urls:
        results = asyncio.run(check_urls(sorted({str(s.source_url) for s in samples})))
        for u, res in results.items():
            log.info("%-8s %s", res.split()[0], u)
        if any(not v.startswith("ok") for v in results.values()):
            sys.exit("Some source URLs did not load; fix them before writing")

    if a.write:
        with TABLE.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=TABLE_FIELDS)
            w.writeheader()
            w.writerows(r.model_dump(mode="json") for r in merged)
        log.info("wrote %s; now run: python -m scripts.seed", TABLE)
    else:
        log.info("preview only; add --write to update %s", TABLE)


if __name__ == "__main__":
    main()
