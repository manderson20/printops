"""The suggestion tool's half: which places get offered, and why."""

from app.roadtrip.places import BANDS, Place, all_places, suggest

BROOKFIELD = (39.7864, -93.0735)


def test_the_bundled_list_loads():
    places = all_places()
    assert len(places) > 1000
    assert all(isinstance(place, Place) for place in places[:5])


def test_places_are_named_the_way_a_reader_would_name_them():
    """GeoNames carries a two-letter state code for the US and a bare
    number elsewhere. "Toronto, 08" is not a place anybody recognises."""
    assert Place("Columbia", "MO", "US", 38.9, -92.3, 100).label == "Columbia, MO"
    assert Place("Toronto", "", "CA", 43.7, -79.4, 100).label == "Toronto, Canada"
    assert Place("Monterrey", "", "MX", 25.7, -100.3, 100).label == "Monterrey, Mexico"


def test_suggestions_expand_outward():
    suggestions = suggest(home_latitude=BROOKFIELD[0], home_longitude=BROOKFIELD[1], seed=1)
    distances = [s.straight_line_miles for s in suggestions]
    assert distances == sorted(distances)
    assert len(distances) > 1


def test_every_suggestion_falls_inside_a_band():
    suggestions = suggest(home_latitude=BROOKFIELD[0], home_longitude=BROOKFIELD[1], seed=3)
    for suggestion in suggestions:
        assert any(lower <= suggestion.straight_line_miles < upper for lower, upper in BANDS), (
            suggestion
        )


def test_the_same_seed_gives_the_same_trip():
    """A page reload must not reshuffle a list somebody is reading."""
    first = suggest(home_latitude=BROOKFIELD[0], home_longitude=BROOKFIELD[1], seed=42)
    second = suggest(home_latitude=BROOKFIELD[0], home_longitude=BROOKFIELD[1], seed=42)
    assert [s.place.label for s in first] == [s.place.label for s in second]


def test_different_seeds_give_different_trips():
    labels = {
        tuple(
            s.place.label
            for s in suggest(home_latitude=BROOKFIELD[0], home_longitude=BROOKFIELD[1], seed=seed)
        )
        for seed in range(12)
    }
    assert len(labels) > 1


def test_places_already_on_the_ladder_are_not_offered_again():
    first = suggest(home_latitude=BROOKFIELD[0], home_longitude=BROOKFIELD[1], seed=5)
    taken = frozenset(s.place.label for s in first)

    again = suggest(
        home_latitude=BROOKFIELD[0],
        home_longitude=BROOKFIELD[1],
        taken_labels=taken,
        seed=5,
    )
    assert not (taken & {s.place.label for s in again})


def test_a_band_with_nothing_in_it_is_skipped_not_filled():
    """A district with nothing between 25 and 60 miles away genuinely has
    nothing there, and inventing a rung would have the ladder claim a
    place is close when it is not.

    Brookfield is the real case: the nearest town of 15,000 is Kirksville
    at 38 miles, so the first band has one candidate and the ladder simply
    starts there rather than manufacturing something nearer."""
    suggestions = suggest(home_latitude=BROOKFIELD[0], home_longitude=BROOKFIELD[1], seed=1)
    assert len(suggestions) < len(BANDS) or all(
        s.straight_line_miles >= BANDS[0][0] for s in suggestions
    )

    # Far enough from North America that every band is empty.
    assert suggest(home_latitude=-40.0, home_longitude=-140.0, seed=1) == []


def test_a_place_no_road_reaches_can_still_be_suggested():
    """Hawaii is 1,700 miles from a point in the Pacific and no car gets
    there. The list does not know about roads — the routing service is
    what discovers that, and the bulk add reports it as skipped rather
    than pretending the drive exists."""
    offshore = suggest(home_latitude=0.0, home_longitude=-170.0, seed=1)
    assert [s.place.label for s in offshore] == ["Pearl City, HI"]


def test_no_suggestion_repeats_within_one_set():
    suggestions = suggest(home_latitude=BROOKFIELD[0], home_longitude=BROOKFIELD[1], seed=9)
    labels = [s.place.label for s in suggestions]
    assert len(labels) == len(set(labels))
