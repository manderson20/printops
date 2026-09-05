"""Places worth driving to, and how to pick a ladder of them.

An admin can type a destination in by hand, and for the near ones they
have to: the bundled list starts at 15,000 people, so the small town
fifteen minutes down the road is not in it. That is the intended
division of labour — you name the two or three local places yourself,
because you know them, and this suggests the ones further out that
nobody has to know.

Suggestions expand outward in bands rather than filling the near
distances, because a ladder is only interesting if each rung is
meaningfully further than the last. Within a band the choice is random
among the largest candidates, so pressing the button again offers a
different trip rather than the same one.
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
