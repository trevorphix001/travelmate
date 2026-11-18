"""Short place summaries from the Wikipedia REST API; only for places with a wikipedia/wikidata tag."""
import logging
from datetime import timedelta
from urllib.parse import quote

from app.core import cache
from app.tools.http import get_json

log = logging.getLogger(__name__)
MAX_CHARS = 300


async def _wikidata_to_title(qid: str) -> tuple[str, str] | None:
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
    data = await cache.get_or_fetch("wikipedia", {"u": url}, lambda: get_json("wikipedia", url), timedelta(days=30))
    links = data.get("entities", {}).get(qid, {}).get("sitelinks", {})
    if "enwiki" in links:
        return "en", links["enwiki"]["title"]
    return None


async def summary(title_or_wikidata: str) -> dict | None:
    """Accepts 'en:Title' (OSM wikipedia tag) or 'Q123' (wikidata). Returns {extract, url} or None."""
    try:
        if title_or_wikidata.startswith("Q") and title_or_wikidata[1:].isdigit():
            resolved = await _wikidata_to_title(title_or_wikidata)
            if not resolved:
                return None
            lang, title = resolved
        else:
            lang, _, title = title_or_wikidata.partition(":")
            if not title:
                lang, title = "en", title_or_wikidata
        url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'), safe='')}"
        data = await cache.get_or_fetch("wikipedia", {"u": url}, lambda: get_json("wikipedia", url),
                                        timedelta(days=30))
    except Exception as e:  # noqa: BLE001 - summaries are optional decoration
        log.info("wiki summary failed for %s: %s", title_or_wikidata, e)
        return None
    extract = (data.get("extract") or "").strip()
    if not extract:
        return None
    if len(extract) > MAX_CHARS:
        extract = extract[: MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"
    page = data.get("content_urls", {}).get("desktop", {}).get("page")
    return {"extract": extract, "url": page}
