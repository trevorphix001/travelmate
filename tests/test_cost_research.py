from datetime import date

import pytest
from pydantic import ValidationError

from scripts.cost_research import Sample, aggregate, merge

from .fakes import COST_ROWS


def s(price, url="https://example.test/a", item="budget", note="", day=1):
    return Sample(city_tier=2, category="stay", item=item, unit="room_night", price_inr=price, source_url=url,
                  checked_on=date(2026, 11, day), note=note)


def test_aggregate_min_median_max_with_citation():
    rows = aggregate([s(900, note="listing A"), s(1500, "https://example.test/b", day=5), s(1200)], "v1")
    assert len(rows) == 1
    r = rows[0]
    assert (r.min_inr, r.typical_inr, r.max_inr) == (900, 1200, 1500)
    assert r.source_url == "https://example.test/a" and r.checked_on == date(2026, 11, 5)
    assert r.source_note.startswith("3 sample(s): listing A") and not r.unverified


def test_merge_replaces_only_matching_rows():
    new = aggregate([s(1000)], "v1")
    merged = merge(COST_ROWS, new)
    assert len(merged) == len(COST_ROWS)
    stay2 = [r for r in merged if (r.category, r.item, r.city_tier) == ("stay", "budget", 2)]
    assert stay2 == new and sum(r.unverified for r in merged) == len(COST_ROWS) - 1


def test_sample_requires_real_url():
    with pytest.raises(ValidationError):
        s(100, url="")
    with pytest.raises(ValidationError):
        s(100, url="not a url")
