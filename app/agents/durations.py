"""Default visit duration (minutes) per OSM kind. Planning assumptions, not facts shown as data."""

VISIT_MINUTES: dict[str, int] = {
    "waterfall": 60,  # walk down to the viewing area, photos, walk back up
    "peak": 75,  # short summit walk plus time at the top
    "viewpoint": 30,  # stop, look, photograph
    "cave_entrance": 60,  # a guided or self-guided walk through the first chambers
    "park": 60,  # a stroll around the main paths
    "nature_reserve": 120,  # a trail or safari slot needs a couple of hours
    "museum": 90,  # one or two galleries at a normal pace
    "fort": 90,  # walls, gates and a viewpoint
    "castle": 90,  # same scale as a fort
    "palace": 90,  # main halls plus grounds
    "monument": 30,  # a single structure
    "memorial": 30,  # a single structure
    "ruins": 60,  # walking an open site
    "archaeological_site": 75,  # larger open site with signage
    "place_of_worship": 45,  # visit, darshan/prayer queue, footwear counters
    "restaurant": 60,  # a sit-down meal
    "cafe": 45,  # coffee and a snack
    "marketplace": 60,  # browsing stalls
    "mall": 90,  # browsing plus a food court
    "attraction": 60,  # unknown attraction: one hour is a neutral default
}
DEFAULT_MINUTES = 60  # same neutral default for kinds not listed


def visit_minutes(kind: str) -> int:
    return VISIT_MINUTES.get(kind, DEFAULT_MINUTES)
