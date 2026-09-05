"""Places worth driving to, and how to pick a ladder of them.

The list has two jobs and they want different things from it.

**Search** wants the small town fifteen minutes down the road. Marceline
has 2,200 people and is the first place this district would look for, so
the bundled list goes down to 1,000 — it is a gazetteer, and a gazetteer
that only knows cities is not much use to a rural district.

**Suggestions** want somewhere recognisable. A village nobody outside the
county has heard of is a worse milestone than none at all, so those are
filtered to `SUGGESTION_MIN_POPULATION` and up, and expand outward in
bands: a ladder is only interesting if each rung is meaningfully further
than the last. Within a band the choice is random among the largest
candidates, so pressing the button again offers a different trip rather
than the same one.
"""

import json
import math
import random
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DATA_PATH = Path(__file__).parent / "data" / "places.json"

# Straight-line miles. The bands are roughly geometric — each about two
# and a half times the last — so a ladder built from them reads as a
# journey that keeps going somewhere new rather than a list of suburbs.
# The first band starts at 25 miles because anything closer is a place
# the admin already named.
BANDS: tuple[tuple[float, float], ...] = (
    (25.0, 60.0),
    (60.0, 150.0),
    (150.0, 375.0),
    (375.0, 800.0),
    (800.0, 1_500.0),
    (1_500.0, 2_500.0),
)

# How many of the biggest places in a band to choose from. Larger makes
# the suggestions more varied and less recognisable; this is roughly the
# point where every candidate is still a place somebody has heard of.
CANDIDATES_PER_BAND = 8

# Suggestions come from places of at least this size. Search sees the
# whole list — see this module's docstring on why the two differ.
SUGGESTION_MIN_POPULATION = 15_000

# A search returns at most this many. Enough to scroll and choose from,
# short enough that "Springfield" is a list rather than a directory.
MAX_SEARCH_RESULTS = 25

EARTH_RADIUS_MILES = 3958.8

_COUNTRY_NAMES = {"US": "USA", "CA": "Canada", "MX": "Mexico"}


@dataclass(frozen=True)
class Place:
    name: str
    admin1: str
    country: str
    latitude: float
    longitude: float
    population: int

    @property
    def label(self) -> str:
        """How the place is named on its own — "Columbia, MO", "Toronto,
        Canada". GeoNames carries a two-letter state code for US rows and
        a bare number for Canadian and Mexican ones, so those are named by
        country instead: "Toronto, 08" is not a place anybody recognises."""
        if self.admin1:
            return f"{self.name}, {self.admin1}"
        return f"{self.name}, {_COUNTRY_NAMES.get(self.country, self.country)}"


@lru_cache(maxsize=1)
def all_places() -> tuple[Place, ...]:
    """The bundled list, read once per process.

    Rows are fixed-length arrays on disk (see data/ATTRIBUTION.md) purely
    for size; they become real objects here so nothing downstream indexes
    into a list by position.
    """
    with _DATA_PATH.open(encoding="utf-8") as handle:
        rows = json.load(handle)
    return tuple(
        Place(
            name=row[0],
            admin1=row[1],
            country=row[2],
            latitude=row[3],
            longitude=row[4],
            population=row[5],
        )
        for row in rows
    )


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(min(1.0, math.sqrt(a)))


@dataclass(frozen=True)
class Suggestion:
    place: Place
    straight_line_miles: float


def suggest(
    *,
    home_latitude: float,
    home_longitude: float,
    taken_labels: frozenset[str] = frozenset(),
    seed: int | None = None,
    bands: tuple[tuple[float, float], ...] = BANDS,
) -> list[Suggestion]:
    """One place per band, nearest first.

    `taken_labels` are destinations the district already has, matched
    case-insensitively on the place's own label, so pressing the button
    twice does not offer Columbia again when Columbia is already a rung.

    Only places of at least `SUGGESTION_MIN_POPULATION`, unlike search:
    a milestone should be somewhere a reader recognises.

    A band with no candidate is skipped rather than filled from a
    neighbouring one. A district in the middle of Nevada genuinely has
    nothing between 25 and 60 miles away, and inventing a rung there
    would mean the ladder claiming a place is close when it is not.

    `seed` makes a suggestion set reproducible, which is what lets a
    caller offer the same list back after a page reload instead of
    quietly reshuffling under someone reading it.
    """
    rng = random.Random(seed)
    taken = {label.casefold() for label in taken_labels}

    measured = [
        (place, _haversine_miles(home_latitude, home_longitude, place.latitude, place.longitude))
        for place in all_places()
        if place.population >= SUGGESTION_MIN_POPULATION
    ]

    suggestions: list[Suggestion] = []
    for lower, upper in bands:
        candidates = [
            (place, miles)
            for place, miles in measured
            if lower <= miles < upper and place.label.casefold() not in taken
        ]
        if not candidates:
            continue
        # Biggest first, then a random one of the top few: the point is a
        # place people recognise, not the nearest place that qualifies.
        candidates.sort(key=lambda pair: -pair[0].population)
        place, miles = rng.choice(candidates[:CANDIDATES_PER_BAND])
        suggestions.append(Suggestion(place=place, straight_line_miles=round(miles, 1)))
        taken.add(place.label.casefold())

    return suggestions


# GeoNames writes these out in full and nobody types them that way.
# Expanded on both sides, so "St Joseph", "St. Joseph" and "Saint Joseph"
# are one search.
_ABBREVIATIONS = {"st": "saint", "ste": "sainte", "mt": "mount", "ft": "fort"}


def _loose(text: str) -> str:
    """Lower-cased, with commas and periods dropped, runs of space
    collapsed, and the handful of abbreviations people type spelled out.
    Applied to both the query and the candidate so the two are compared on
    what somebody meant rather than how they wrote it."""
    words = text.replace(",", " ").replace(".", " ").replace("-", " ").split()
    return " ".join(_ABBREVIATIONS.get(word.casefold(), word.casefold()) for word in words)


def search(
    query: str,
    *,
    home_latitude: float | None = None,
    home_longitude: float | None = None,
    limit: int = MAX_SEARCH_RESULTS,
) -> list[Suggestion]:
    """Places whose name matches `query`, nearest-and-biggest first.

    Matched against both the bare name and the name with its state, with
    punctuation ignored on both sides — so "marceline", "marceline, mo"
    and "marceline mo" all find it, and "springfield il" narrows a name
    that thirty places share. Nobody types the comma.

    Ranked by how the match was made before anything else: a place whose
    name *starts* with what was typed comes before one that merely
    contains it, because somebody typing "york" means York far more often
    than they mean New York. Within a rank, closer to home first when
    home is known — a district searching "washington" almost always wants
    the one in their own state — and by population when it is not.

    Unlike suggestions, this sees the whole list down to a thousand
    people. The small town down the road is exactly what somebody would
    search for.
    """
    needle = _loose(query)
    if len(needle) < 2:
        return []

    scored: list[tuple[int, float, Place]] = []
    for place in all_places():
        name = _loose(place.name)
        label = _loose(place.label)
        if name.startswith(needle) or label.startswith(needle):
            rank = 0
        elif needle in name or needle in label:
            rank = 1
        else:
            continue
        if home_latitude is not None and home_longitude is not None:
            distance = _haversine_miles(
                home_latitude, home_longitude, place.latitude, place.longitude
            )
            scored.append((rank, distance, place))
        else:
            # No home to measure from, so the biggest first — negated so
            # one sort key means "better" in both cases.
            scored.append((rank, -place.population, place))

    scored.sort(key=lambda row: (row[0], row[1]))

    results: list[Suggestion] = []
    for _, measure, place in scored[:limit]:
        miles = (
            round(measure, 1) if home_latitude is not None and home_longitude is not None else 0.0
        )
        results.append(Suggestion(place=place, straight_line_miles=miles))
    return results
