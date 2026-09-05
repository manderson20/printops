"""Unit tests for the road trip — the ladder a district owns, and the
route drawn from it. No DB, same shape as test_reports_equivalency.py:
everything here is a pure function of rows the caller already fetched."""

import pytest

from app.models.destination import Destination
from app.models.location import Location
from app.reports.equivalency_config import DISTANCE_LADDER, FEET_PER_MILE
from app.reports.road_trip import (
    build_route,
    haversine_miles,
    ladder_from_destinations,
)

BROOKFIELD = (39.7864, -93.0735)
MARCELINE = (39.7117, -92.9477)
JEFFERSON_CITY = (38.5767, -92.1735)


def _home(latitude=BROOKFIELD[0], longitude=BROOKFIELD[1]) -> Location:
    return Location(name="Brookfield R-III", latitude=latitude, longitude=longitude, is_home=True)


def _destination(
    name,
    miles,
    coordinates=None,
    *,
    short_name=None,
    enabled=True,
    position=None,
    leg_miles=None,
    geometry=None,
) -> Destination:
    latitude, longitude = coordinates if coordinates else (None, None)
    return Destination(
        name=name,
        short_name=short_name,
        miles=miles,
        latitude=latitude,
        longitude=longitude,
        enabled=enabled,
        position=position,
        leg_miles=leg_miles,
        route_geometry=geometry,
    )


# --- distance ----------------------------------------------------------


def test_straight_line_is_shorter_than_the_drive():
    """The figure offered to an admin is a floor to correct upward, never
    an answer to accept — 120 road miles to Jefferson City against about
    96 as the crow flies."""
    assert haversine_miles(*BROOKFIELD, *JEFFERSON_CITY) < 120.0


def test_distance_to_itself_is_zero():
    assert haversine_miles(*BROOKFIELD, *BROOKFIELD) == 0.0


# --- the ladder --------------------------------------------------------


def test_ladder_is_built_from_configured_destinations():
    ladder = ladder_from_destinations(
        [_destination("Brookfield to Marceline", 12.0, MARCELINE, short_name="Marceline")]
    )
    assert [rung.name for rung in ladder.rungs] == ["Brookfield to Marceline"]
    assert ladder.rungs[0].value == 12.0 * FEET_PER_MILE
    assert ladder.rungs[0].label == "Marceline"


def test_a_destination_that_is_switched_off_is_not_a_rung():
    ladder = ladder_from_destinations(
        [
            _destination("kept", 10.0),
            _destination("paused", 20.0, enabled=False),
        ]
    )
    assert [rung.name for rung in ladder.rungs] == ["kept"]


def test_no_destinations_falls_back_to_the_built_in_ladder():
    """An installation whose admin has never opened the Road Trip page
    still gets fun facts."""
    assert ladder_from_destinations([]) is DISTANCE_LADDER


def test_every_destination_switched_off_falls_back_too():
    assert ladder_from_destinations([_destination("paused", 5.0, enabled=False)]) is DISTANCE_LADDER


def test_a_rung_without_coordinates_still_counts():
    """ "all the way to the Moon" is a distance, not a place — it belongs
    on the ladder and never on the map."""
    ladder = ladder_from_destinations([_destination("all the way to the Moon", 238_900.0)])
    assert ladder.rungs[0].value == 238_900.0 * FEET_PER_MILE


# --- the route ---------------------------------------------------------


def test_no_home_means_no_map():
    assert build_route(None, [_destination("Marceline", 12.0, MARCELINE)], 100_000.0) is None


def test_home_without_coordinates_means_no_map():
    route = build_route(
        _home(latitude=None, longitude=None),
        [_destination("Marceline", 12.0, MARCELINE)],
        100_000.0,
    )
    assert route is None


def test_destinations_without_coordinates_mean_no_map():
    route = build_route(_home(), [_destination("coast to coast", 2_800.0)], 100_000.0)
    assert route is None


def test_stops_are_ordered_by_distance_and_flagged_as_reached():
    route = build_route(
        _home(),
        [
            _destination("Brookfield to Jefferson City", 120.0, JEFFERSON_CITY),
            _destination("Brookfield to Marceline", 12.0, MARCELINE),
        ],
        20.0 * FEET_PER_MILE,
    )
    assert [stop.miles for stop in route.stops] == [12.0, 120.0]
    assert [stop.reached for stop in route.stops] == [True, False]


def test_unplottable_rungs_are_left_out_of_the_route():
    route = build_route(
        _home(),
        [
            _destination("Brookfield to Marceline", 12.0, MARCELINE),
            _destination("all the way across Missouri", 300.0),
        ],
        20.0 * FEET_PER_MILE,
    )
    assert [stop.name for stop in route.stops] == ["Brookfield to Marceline"]


def test_a_journey_that_has_not_started_has_no_marker():
    """Zero pages is not "at home about to leave" — a pin on the school
    would read as progress nobody has made."""
    route = build_route(_home(), [_destination("Marceline", 12.0, MARCELINE)], 0.0)
    assert route.position is None


def test_the_marker_sits_between_home_and_the_first_stop():
    route = build_route(_home(), [_destination("Marceline", 12.0, MARCELINE)], 6.0 * FEET_PER_MILE)
    assert route.position.from_label is None
    assert route.position.to_label == "Marceline"
    # Halfway there by mileage, so halfway along the line by construction.
    assert route.position.latitude == (BROOKFIELD[0] + MARCELINE[0]) / 2
    assert route.position.longitude == (BROOKFIELD[1] + MARCELINE[1]) / 2


def test_the_marker_names_the_leg_it_is_on():
    route = build_route(
        _home(),
        [
            _destination("Brookfield to Marceline", 12.0, MARCELINE, short_name="Marceline"),
            _destination(
                "Brookfield to Jefferson City",
                120.0,
                JEFFERSON_CITY,
                short_name="Jefferson City",
            ),
        ],
        66.0 * FEET_PER_MILE,
    )
    assert route.position.from_label == "Marceline"
    assert route.position.to_label == "Jefferson City"


def test_past_the_last_pin_the_marker_stops_there():
    """Extrapolating past the last plottable stop would put the marker in
    the ocean; it stays on the place that was actually reached."""
    route = build_route(
        _home(), [_destination("Marceline", 12.0, MARCELINE)], 5_000.0 * FEET_PER_MILE
    )
    assert (route.position.latitude, route.position.longitude) == MARCELINE
    assert route.position.to_label is None


def test_miles_travelled_is_the_distance_converted():
    route = build_route(_home(), [_destination("Marceline", 12.0, MARCELINE)], 5_280.0)
    assert route.miles_travelled == 1.0


# --- one trip, in order ------------------------------------------------


def test_waypoints_come_back_in_the_order_they_are_driven():
    """Not by distance. An admin who sends the trip somewhere further
    before somewhere nearer is describing a real journey."""
    route = build_route(
        _home(),
        [
            _destination("Jefferson City", 122.6, JEFFERSON_CITY, position=1, leg_miles=122.6),
            _destination("Marceline", 134.7, MARCELINE, position=2, leg_miles=12.1),
        ],
        50.0 * FEET_PER_MILE,
    )
    assert [stop.label for stop in route.stops] == ["Jefferson City", "Marceline"]
    assert [stop.position for stop in route.stops] == [1, 2]


def test_a_leg_is_the_drive_from_the_previous_waypoint():
    route = build_route(
        _home(),
        [
            _destination("Marceline", 12.1, MARCELINE, position=1, leg_miles=12.1),
            _destination("Jefferson City", 129.9, JEFFERSON_CITY, position=2, leg_miles=117.8),
        ],
        20.0 * FEET_PER_MILE,
    )
    assert [stop.leg_miles for stop in route.stops] == [12.1, 117.8]
    assert [stop.miles for stop in route.stops] == [12.1, 129.9]


def test_a_leg_falls_back_to_the_gap_between_running_totals():
    """A ladder of typed distances, never driven, still produces sensible
    legs rather than zeroes."""
    route = build_route(
        _home(),
        [
            _destination("Marceline", 12.0, MARCELINE, position=1),
            _destination("Jefferson City", 130.0, JEFFERSON_CITY, position=2),
        ],
        20.0 * FEET_PER_MILE,
    )
    assert [stop.leg_miles for stop in route.stops] == [12.0, 118.0]


def test_the_marker_rides_the_leg_it_is_on():
    """Half a leg in by mileage is half a leg along by geometry — measured
    on the leg, not on the whole trip."""
    leg_two = [[39.7117, -92.9477], [38.5767, -92.1735]]
    route = build_route(
        _home(),
        [
            _destination("Marceline", 12.0, MARCELINE, position=1, leg_miles=12.0),
            _destination(
                "Jefferson City",
                130.0,
                JEFFERSON_CITY,
                position=2,
                leg_miles=118.0,
                geometry=leg_two,
            ),
        ],
        71.0 * FEET_PER_MILE,
    )
    assert route.position.from_label == "Marceline"
    assert route.position.to_label == "Jefferson City"
    # 59 miles into a 118-mile leg: halfway along it.
    assert route.position.latitude == pytest.approx(
        (MARCELINE[0] + JEFFERSON_CITY[0]) / 2, abs=1e-6
    )


def test_a_trip_that_doubles_back_keeps_both_legs():
    """Marceline is north-west of Brookfield and Jefferson City is
    south-east, so leg two retraces leg one before carrying on. Both legs
    keep their own shape; the numbered pins say which way round it went."""
    route = build_route(
        _home(),
        [
            _destination(
                "Marceline",
                12.1,
                MARCELINE,
                position=1,
                leg_miles=12.1,
                geometry=[[39.7864, -93.0735], [39.7117, -92.9477]],
            ),
            _destination(
                "Jefferson City",
                129.9,
                JEFFERSON_CITY,
                position=2,
                leg_miles=117.8,
                geometry=[[39.7117, -92.9477], [39.7864, -93.0735], [38.5767, -92.1735]],
            ),
        ],
        200.0 * FEET_PER_MILE,
    )
    assert len(route.stops) == 2
    assert all(stop.geometry for stop in route.stops)
    assert route.stops[1].geometry[1] == [39.7864, -93.0735]


def test_a_rung_with_no_coordinates_is_not_a_waypoint():
    route = build_route(
        _home(),
        [
            _destination("Marceline", 12.1, MARCELINE, position=1, leg_miles=12.1),
            _destination("all the way to the Moon", 238_900.0),
        ],
        20.0 * FEET_PER_MILE,
    )
    assert [stop.label for stop in route.stops] == ["Marceline"]
