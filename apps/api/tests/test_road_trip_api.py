"""The Settings side of the road trip: the district's places and the
rungs measured from them."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.base import Base
from app.models.printer import Printer
from app.roadtrip.routing import DrivingRoute, Itinerary, RoutingError


@pytest_asyncio.fixture
async def db_session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def client(db_session_factory):
    async def override_get_db():
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def auth_headers(client):
    response = client.post("/auth/login", json={"username": "admin", "password": "changeme"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


BROOKFIELD = {"latitude": 39.7864, "longitude": -93.0735}
MARCELINE = {"latitude": 39.7117, "longitude": -92.9477}
JEFFERSON_CITY = {"latitude": 38.5767, "longitude": -92.1735}

# A stand-in for the road network, with the real leg distances for the
# trip these tests use: Brookfield to Marceline is 12.1 miles, and
# Marceline on to Jefferson City is another 117.8 — which doubles back
# past Brookfield, since Marceline is north-west and Jefferson City is
# south-east.
LEG_ONE = DrivingRoute(miles=12.1, geometry=[[39.7864, -93.0735], [39.7117, -92.9477]])
LEG_TWO = DrivingRoute(
    miles=117.8, geometry=[[39.7117, -92.9477], [39.2, -92.6], [38.5767, -92.1735]]
)


def _trip(*legs: DrivingRoute) -> Itinerary:
    return Itinerary(total_miles=round(sum(leg.miles for leg in legs), 1), legs=list(legs))


@pytest.fixture
def trip_answers(monkeypatch):
    """Swaps the routing call out.

    Nothing in this file touches a real routing service: what is worth
    testing is what PrintOps does with an answer, not whether somebody's
    demo server was up during CI. The stub returns as many legs as it was
    asked for hops, because the real one does and the caller checks.
    """

    def install(result):
        async def fake(*, base_url, points):
            if isinstance(result, Exception):
                raise result
            hops = len(points) - 1
            legs = list(result.legs)[:hops]
            while len(legs) < hops:
                legs.append(LEG_TWO)
            return Itinerary(total_miles=round(sum(leg.miles for leg in legs), 1), legs=legs)

        monkeypatch.setattr("app.roadtrip.itinerary.fetch_itinerary", fake)

    return install


@pytest.fixture(autouse=True)
def routing_off_by_default(trip_answers):
    """Every test that does not say otherwise gets a routing service that
    cannot be reached, so a test which forgot to stub it fails loudly
    rather than reaching the internet from CI."""
    trip_answers(RoutingError("no routing service in tests"))


def _make_location(client, auth_headers, **fields):
    body = {"name": "High School"} | fields
    return client.post("/api/v1/road-trip/locations", headers=auth_headers, json=body)


def _make_destination(client, auth_headers, **fields):
    body = {"name": "Brookfield to Marceline", "miles": 12.0} | fields
    return client.post("/api/v1/road-trip/destinations", headers=auth_headers, json=body)


# --- access ------------------------------------------------------------


def test_locations_are_admin_only(client):
    assert client.get("/api/v1/road-trip/locations").status_code == 401


def test_destinations_are_admin_only(client):
    assert client.get("/api/v1/road-trip/destinations").status_code == 401


# --- locations ---------------------------------------------------------


def test_a_location_round_trips(client, auth_headers):
    created = _make_location(
        client, auth_headers, street="1000 Pershing Rd", city="Brookfield", state="MO"
    )
    assert created.status_code == 201
    assert created.json()["street"] == "1000 Pershing Rd"

    listed = client.get("/api/v1/road-trip/locations", headers=auth_headers).json()
    assert [location["name"] for location in listed] == ["High School"]


def test_two_locations_cannot_share_a_name(client, auth_headers):
    _make_location(client, auth_headers)
    assert _make_location(client, auth_headers).status_code == 409


def test_half_a_coordinate_is_rejected(client, auth_headers):
    """Rejected at the door so every consumer can treat "has a latitude"
    as "can be drawn"."""
    response = _make_location(client, auth_headers, latitude=39.7864)
    assert response.status_code == 422


def test_a_latitude_off_the_earth_is_rejected(client, auth_headers):
    response = _make_location(client, auth_headers, latitude=200.0, longitude=0.0)
    assert response.status_code == 422


def test_setting_a_new_home_clears_the_old_one(client, auth_headers):
    first = _make_location(client, auth_headers, name="Admin", is_home=True).json()
    second = _make_location(client, auth_headers, name="High School", is_home=True).json()

    listed = client.get("/api/v1/road-trip/locations", headers=auth_headers).json()
    homes = [location["id"] for location in listed if location["is_home"]]
    assert homes == [second["id"]]
    assert first["id"] not in homes


def test_home_is_listed_first(client, auth_headers):
    _make_location(client, auth_headers, name="Ainsworth")
    _make_location(client, auth_headers, name="Zion", is_home=True)

    listed = client.get("/api/v1/road-trip/locations", headers=auth_headers).json()
    assert listed[0]["name"] == "Zion"


def test_a_location_can_be_deleted_even_when_it_is_home(client, auth_headers):
    """Refusing would mean a building that closed cannot be removed until
    another is nominated. The cost is the map, not the facts."""
    home = _make_location(client, auth_headers, is_home=True).json()
    # The call is its own statement: an API call inside an assert vanishes
    # when Python is run with -O, taking the delete with it. Same reason
    # #fix/side-effect-in-assert took them out of the rest of the suite.
    response = client.delete(f"/api/v1/road-trip/locations/{home['id']}", headers=auth_headers)
    assert response.status_code == 204


def test_clearing_one_half_of_a_coordinate_pair_is_rejected(client, auth_headers):
    location = _make_location(client, auth_headers, latitude=39.0, longitude=-93.0).json()
    response = client.patch(
        f"/api/v1/road-trip/locations/{location['id']}",
        headers=auth_headers,
        json={"latitude": None},
    )
    assert response.status_code == 422


# --- unmatched buildings ----------------------------------------------


@pytest_asyncio.fixture
async def printers_in_two_buildings(db_session_factory):
    async with db_session_factory() as session:
        session.add_all(
            [
                Printer(name="HS Office", ip_address="10.0.0.1", building="High School"),
                Printer(name="HS Library", ip_address="10.0.0.2", building="High School"),
                Printer(name="Elem Office", ip_address="10.0.0.3", building="Elementary"),
                Printer(name="Nowhere", ip_address="10.0.0.4"),
            ]
        )
        await session.commit()


def test_unmatched_buildings_are_the_ones_with_no_location(
    client, auth_headers, printers_in_two_buildings
):
    _make_location(client, auth_headers, name="High School")

    unmatched = client.get(
        "/api/v1/road-trip/locations/unmatched-buildings", headers=auth_headers
    ).json()
    assert [row["building"] for row in unmatched] == ["Elementary"]
    assert unmatched[0]["printer_count"] == 1


def test_matching_ignores_case_and_stray_whitespace(
    client, auth_headers, printers_in_two_buildings
):
    """Two free-text fields typed by different people at different times.
    "high school " matching "High School" is not a coincidence."""
    _make_location(client, auth_headers, name="high school ")
    _make_location(client, auth_headers, name="ELEMENTARY")

    unmatched = client.get(
        "/api/v1/road-trip/locations/unmatched-buildings", headers=auth_headers
    ).json()
    assert unmatched == []


# --- destinations ------------------------------------------------------


def test_a_destination_round_trips(client, auth_headers):
    created = _make_destination(client, auth_headers, short_name="Marceline")
    assert created.status_code == 201
    assert created.json()["short_name"] == "Marceline"


def test_destinations_come_back_in_travelled_order(client, auth_headers):
    _make_destination(client, auth_headers, name="Chicago", miles=410.0)
    _make_destination(client, auth_headers, name="Marceline", miles=12.0)

    listed = client.get("/api/v1/road-trip/destinations", headers=auth_headers).json()
    assert [row["name"] for row in listed] == ["Marceline", "Chicago"]


def test_a_destination_needs_a_positive_distance(client, auth_headers):
    assert _make_destination(client, auth_headers, miles=0).status_code == 422


def test_the_straight_line_distance_is_offered_but_never_stored(client, auth_headers):
    """The settings page shows it as the floor the driving distance must
    sit above — 120 road miles against about 96 in a straight line."""
    _make_location(
        client,
        auth_headers,
        name="Brookfield R-III",
        latitude=39.7864,
        longitude=-93.0735,
        is_home=True,
    )
    created = _make_destination(
        client,
        auth_headers,
        name="Brookfield to Jefferson City",
        miles=120.0,
        latitude=38.5767,
        longitude=-92.1735,
    ).json()

    assert created["miles"] == 120.0
    assert 90.0 < created["straight_line_miles"] < 100.0


def test_no_home_means_no_straight_line_figure(client, auth_headers):
    created = _make_destination(client, auth_headers, latitude=39.7117, longitude=-92.9477).json()
    assert created["straight_line_miles"] is None


def test_a_destination_can_be_paused_without_losing_the_figure(client, auth_headers):
    destination = _make_destination(client, auth_headers).json()
    updated = client.patch(
        f"/api/v1/road-trip/destinations/{destination['id']}",
        headers=auth_headers,
        json={"enabled": False},
    ).json()
    assert updated["enabled"] is False
    assert updated["miles"] == 12.0


# --- one trip, in order ------------------------------------------------


def _home_with_coordinates(client, auth_headers):
    return _make_location(
        client, auth_headers, name="Brookfield R-III", is_home=True, **BROOKFIELD
    ).json()


def _add(client, auth_headers, name, **fields):
    return client.post(
        "/api/v1/road-trip/destinations", headers=auth_headers, json={"name": name, **fields}
    )


def test_a_waypoints_distance_is_the_trip_so_far_not_the_direct_drive(
    client, auth_headers, trip_answers
):
    """The whole point of waypoints. Jefferson City is 122.6 miles as a
    drive of its own, and 129.9 into a trip that goes via Marceline
    first — and the ladder uses the second, because that is the road
    actually drawn beneath it."""
    _home_with_coordinates(client, auth_headers)
    trip_answers(_trip(LEG_ONE, LEG_TWO))

    _add(client, auth_headers, "Marceline", **MARCELINE)
    _add(client, auth_headers, "Jefferson City", **JEFFERSON_CITY)

    listed = client.get("/api/v1/road-trip/destinations", headers=auth_headers).json()
    assert [d["name"] for d in listed] == ["Marceline", "Jefferson City"]
    assert [d["position"] for d in listed] == [1, 2]
    assert [d["leg_miles"] for d in listed] == [12.1, 117.8]
    assert [d["miles"] for d in listed] == [12.1, 129.9]


def test_a_waypoint_keeps_the_shape_of_its_own_leg(client, auth_headers, trip_answers):
    """Not the drive from home. A leg is what a waypoint owns, and drawing
    legs in order is what lets a doubled-back trip retrace one road."""
    _home_with_coordinates(client, auth_headers)
    trip_answers(_trip(LEG_ONE, LEG_TWO))
    _add(client, auth_headers, "Marceline", **MARCELINE)
    _add(client, auth_headers, "Jefferson City", **JEFFERSON_CITY)

    listed = client.get("/api/v1/road-trip/destinations", headers=auth_headers).json()
    assert all(d["has_route"] for d in listed)


def test_a_new_waypoint_joins_the_end_of_the_trip(client, auth_headers, trip_answers):
    """Where it belongs is a decision for the reorder control, not
    something to infer from how far away it happens to be."""
    _home_with_coordinates(client, auth_headers)
    trip_answers(_trip(LEG_ONE, LEG_TWO))
    _add(client, auth_headers, "Jefferson City", **JEFFERSON_CITY)
    _add(client, auth_headers, "Marceline", **MARCELINE)

    listed = client.get("/api/v1/road-trip/destinations", headers=auth_headers).json()
    assert [d["name"] for d in listed] == ["Jefferson City", "Marceline"]
    assert [d["position"] for d in listed] == [1, 2]


def test_reordering_changes_every_distance_after_the_move(client, auth_headers, trip_answers):
    _home_with_coordinates(client, auth_headers)
    trip_answers(_trip(LEG_ONE, LEG_TWO))
    first = _add(client, auth_headers, "Marceline", **MARCELINE).json()
    second = _add(client, auth_headers, "Jefferson City", **JEFFERSON_CITY).json()

    reordered = client.put(
        "/api/v1/road-trip/destinations/order",
        headers=auth_headers,
        json={"destination_ids": [second["id"], first["id"]]},
    ).json()
    assert [d["name"] for d in reordered] == ["Jefferson City", "Marceline"]
    assert [d["position"] for d in reordered] == [1, 2]
    # Re-driven, so the running totals belong to the new order.
    assert reordered[0]["miles"] < reordered[1]["miles"]


def test_a_partial_order_is_refused(client, auth_headers, trip_answers):
    """A reorder is a statement about the whole trip, not a nudge whose
    meaning depends on what else moved since the page loaded."""
    _home_with_coordinates(client, auth_headers)
    trip_answers(_trip(LEG_ONE, LEG_TWO))
    first = _add(client, auth_headers, "Marceline", **MARCELINE).json()
    _add(client, auth_headers, "Jefferson City", **JEFFERSON_CITY)

    response = client.put(
        "/api/v1/road-trip/destinations/order",
        headers=auth_headers,
        json={"destination_ids": [first["id"]]},
    )
    assert response.status_code == 422


def test_removing_a_waypoint_brings_the_rest_closer(client, auth_headers, trip_answers):
    _home_with_coordinates(client, auth_headers)
    trip_answers(_trip(LEG_ONE, LEG_TWO))
    first = _add(client, auth_headers, "Marceline", **MARCELINE).json()
    _add(client, auth_headers, "Jefferson City", **JEFFERSON_CITY)

    trip_answers(_trip(LEG_TWO))
    client.delete(f"/api/v1/road-trip/destinations/{first['id']}", headers=auth_headers)

    listed = client.get("/api/v1/road-trip/destinations", headers=auth_headers).json()
    assert [d["name"] for d in listed] == ["Jefferson City"]
    assert listed[0]["position"] == 1
    assert listed[0]["miles"] == 117.8


def test_a_rung_with_no_coordinates_is_on_the_ladder_but_not_the_trip(client, auth_headers):
    """ "all the way to the Moon" is a distance, not a place to drive to."""
    created = _add(client, auth_headers, "all the way to the Moon", miles=238900.0)
    assert created.status_code == 201
    body = created.json()
    assert body["position"] is None
    assert body["has_route"] is False
    assert body["miles"] == 238900.0


def test_the_trip_comes_before_the_rungs_beyond_it(client, auth_headers, trip_answers):
    _home_with_coordinates(client, auth_headers)
    trip_answers(_trip(LEG_ONE, LEG_TWO))
    _add(client, auth_headers, "all the way to the Moon", miles=238900.0)
    _add(client, auth_headers, "Marceline", **MARCELINE)

    listed = client.get("/api/v1/road-trip/destinations", headers=auth_headers).json()
    assert [d["name"] for d in listed] == ["Marceline", "all the way to the Moon"]


def test_a_typed_distance_survives_the_trip_being_driven(client, auth_headers, trip_answers):
    """An admin who types a figure because that is what the road signs say
    keeps it — through this drive and every later one."""
    _home_with_coordinates(client, auth_headers)
    trip_answers(_trip(LEG_ONE))

    body = _add(client, auth_headers, "Marceline", miles=15.0, **MARCELINE).json()
    assert body["miles"] == 15.0
    assert body["miles_override"] == 15.0
    assert body["route_miles"] == 12.1

    # The bug this column exists to stop: every recompute used to rewrite
    # `miles` from the route, so an override lasted until the next time
    # anything moved.
    client.post("/api/v1/road-trip/itinerary/route", headers=auth_headers)
    listed = client.get("/api/v1/road-trip/destinations", headers=auth_headers).json()
    assert listed[0]["miles"] == 15.0
    assert listed[0]["route_miles"] == 12.1


def test_an_override_can_be_cleared_to_go_back_to_the_road(client, auth_headers, trip_answers):
    """The edit form submits an empty distance field as null, and there has
    to be a way back from a figure somebody regrets."""
    _home_with_coordinates(client, auth_headers)
    trip_answers(_trip(LEG_ONE))
    created = _add(client, auth_headers, "Marceline", miles=15.0, **MARCELINE).json()

    cleared = client.patch(
        f"/api/v1/road-trip/destinations/{created['id']}",
        headers=auth_headers,
        json={"miles": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["miles_override"] is None
    assert cleared.json()["miles"] == 12.1


def test_resubmitting_a_measured_distance_is_not_an_override(client, auth_headers, trip_answers):
    """The edit form resubmits whatever is displayed. A coordinate change
    must not arrive carrying a measured figure and be mistaken for
    somebody insisting on it."""
    _home_with_coordinates(client, auth_headers)
    trip_answers(_trip(LEG_ONE))
    created = _add(client, auth_headers, "Marceline", **MARCELINE).json()
    assert created["miles"] == 12.1

    updated = client.patch(
        f"/api/v1/road-trip/destinations/{created['id']}",
        headers=auth_headers,
        json={"miles": 12.1, "short_name": "Marceline"},
    ).json()
    assert updated["miles_override"] is None


def test_a_paused_waypoint_leaves_the_road(client, auth_headers, trip_answers):
    """The report hides a paused stop, so routing through it would give
    every later waypoint a leg starting from a place nobody can see."""
    _home_with_coordinates(client, auth_headers)
    trip_answers(_trip(LEG_ONE, LEG_TWO))
    first = _add(client, auth_headers, "Marceline", **MARCELINE).json()
    _add(client, auth_headers, "Jefferson City", **JEFFERSON_CITY)

    trip_answers(_trip(LEG_TWO))
    client.patch(
        f"/api/v1/road-trip/destinations/{first['id']}",
        headers=auth_headers,
        json={"enabled": False},
    )

    listed = client.get("/api/v1/road-trip/destinations", headers=auth_headers).json()
    paused = next(d for d in listed if d["name"] == "Marceline")
    still_on = next(d for d in listed if d["name"] == "Jefferson City")
    assert paused["has_route"] is False
    # Jefferson City is now the first stop on the road, so its running
    # total is its own leg rather than a total that counted a hidden stop.
    assert still_on["miles"] == 117.8


def test_moving_home_re_drives_the_trip(client, auth_headers, trip_answers):
    """Every leg starts from home, so a new home makes every stored
    distance describe a journey from somewhere the trip no longer
    begins."""
    home = _home_with_coordinates(client, auth_headers)
    trip_answers(_trip(LEG_ONE))
    _add(client, auth_headers, "Marceline", **MARCELINE)

    trip_answers(_trip(LEG_TWO))
    client.patch(
        f"/api/v1/road-trip/locations/{home['id']}",
        headers=auth_headers,
        json={"latitude": 38.5767, "longitude": -92.1735},
    )

    listed = client.get("/api/v1/road-trip/destinations", headers=auth_headers).json()
    assert listed[0]["miles"] == 117.8


def test_a_waypoint_with_no_reachable_trip_is_refused(client, auth_headers, trip_answers):
    """Rather than quietly storing nothing, or a straight line dressed up
    as a drive."""
    _home_with_coordinates(client, auth_headers)
    trip_answers(RoutingError("Could not reach the routing service: timeout"))

    response = _add(client, auth_headers, "Marceline", **MARCELINE)
    assert response.status_code == 422
    assert "Couldn't measure the trip" in response.json()["detail"]


def test_the_whole_trip_can_be_driven_again(client, auth_headers, trip_answers):
    """A waypoint added without a distance follows the road the first time
    the trip is driven — which is what the seeded rungs do, since 0073 and
    0074 deliberately make no HTTP calls."""
    _home_with_coordinates(client, auth_headers)
    trip_answers(RoutingError("not yet"))
    created = _add(client, auth_headers, "Marceline", miles=12.0, **MARCELINE).json()
    # Typed only because there was no road to measure; not an override.
    assert created["miles_override"] == 12.0

    # Clear it, the way the edit form does with an empty distance field.
    client.patch(
        f"/api/v1/road-trip/destinations/{created['id']}",
        headers=auth_headers,
        json={"miles": None},
    )

    trip_answers(_trip(LEG_ONE))
    summary = client.post("/api/v1/road-trip/itinerary/route", headers=auth_headers).json()
    assert summary["waypoints"] == 1
    assert summary["total_miles"] == 12.1
    assert summary["error"] is None

    listed = client.get("/api/v1/road-trip/destinations", headers=auth_headers).json()
    assert listed[0]["miles"] == 12.1
    assert listed[0]["miles_override"] is None


def test_a_failed_trip_keeps_the_distances_it_had(client, auth_headers, trip_answers):
    """A trip that blanked itself every time a routing service hiccuped
    would lose a correct answer to a temporary problem."""
    _home_with_coordinates(client, auth_headers)
    _add(client, auth_headers, "Marceline", miles=12.0, **MARCELINE)

    trip_answers(RoutingError("service unavailable"))
    client.post("/api/v1/road-trip/itinerary/route", headers=auth_headers)

    listed = client.get("/api/v1/road-trip/destinations", headers=auth_headers).json()
    assert listed[0]["miles"] == 12.0
    assert "service unavailable" in listed[0]["route_error"]


def test_the_itinerary_summary_reports_the_end_of_the_journey(client, auth_headers, trip_answers):
    _home_with_coordinates(client, auth_headers)
    trip_answers(_trip(LEG_ONE, LEG_TWO))
    _add(client, auth_headers, "Marceline", **MARCELINE)
    _add(client, auth_headers, "Jefferson City", **JEFFERSON_CITY)

    summary = client.get("/api/v1/road-trip/itinerary", headers=auth_headers).json()
    assert summary["waypoints"] == 2
    assert summary["total_miles"] == 129.9
    assert summary["last_driven_at"] is not None


def test_routing_can_be_switched_off(client, auth_headers, trip_answers):
    """For an installation with no outbound internet, every save pausing
    twenty seconds to fail is worse than not trying."""
    _home_with_coordinates(client, auth_headers)
    trip_answers(_trip(LEG_ONE))
    client.put("/api/v1/road-trip/settings", headers=auth_headers, json={"routing_enabled": False})

    body = _add(client, auth_headers, "Marceline", miles=12.0, **MARCELINE).json()
    assert body["has_route"] is False
    assert "switched off" in body["route_error"]


def test_the_routing_service_url_is_a_setting_not_a_constant(client, auth_headers):
    settings = client.get("/api/v1/road-trip/settings", headers=auth_headers).json()
    assert settings["routing_base_url"].startswith("http")

    updated = client.put(
        "/api/v1/road-trip/settings",
        headers=auth_headers,
        json={"routing_base_url": "https://osrm.internal.example.org"},
    ).json()
    assert updated["routing_base_url"] == "https://osrm.internal.example.org"


# --- suggestions -------------------------------------------------------


def test_suggestions_need_a_home_with_coordinates(client, auth_headers):
    response = client.post("/api/v1/road-trip/destinations/suggest", headers=auth_headers)
    assert response.status_code == 422
    assert "home location" in response.json()["detail"]


def test_suggestions_expand_outward_from_home(client, auth_headers):
    _home_with_coordinates(client, auth_headers)

    body = client.post("/api/v1/road-trip/destinations/suggest?seed=1", headers=auth_headers).json()
    distances = [s["straight_line_miles"] for s in body["suggestions"]]
    assert distances == sorted(distances)
    assert body["seed"] == 1
    assert body["suggestions"][0]["name"].startswith("Brookfield R-III to ")


def test_a_suggestion_set_can_be_asked_for_again(client, auth_headers):
    _home_with_coordinates(client, auth_headers)
    first = client.post("/api/v1/road-trip/destinations/suggest", headers=auth_headers).json()
    again = client.post(
        f"/api/v1/road-trip/destinations/suggest?seed={first['seed']}", headers=auth_headers
    ).json()
    assert first["suggestions"] == again["suggestions"]


def test_suggesting_writes_nothing(client, auth_headers):
    _home_with_coordinates(client, auth_headers)
    client.post("/api/v1/road-trip/destinations/suggest", headers=auth_headers)

    assert client.get("/api/v1/road-trip/destinations", headers=auth_headers).json() == []


# --- adding several at once --------------------------------------------


def test_several_waypoints_are_added_and_the_trip_driven_once(client, auth_headers, trip_answers):
    """The reason this endpoint exists. Six separate creates would
    re-drive the trip six times and throw the first five answers away."""
    _home_with_coordinates(client, auth_headers)
    calls = {"n": 0}
    original = _trip(LEG_ONE, LEG_TWO)

    async def counting(*, base_url, points):
        calls["n"] += 1
        hops = len(points) - 1
        legs = list(original.legs)[:hops]
        while len(legs) < hops:
            legs.append(LEG_TWO)
        return Itinerary(total_miles=round(sum(leg.miles for leg in legs), 1), legs=legs)

    import app.roadtrip.itinerary as itinerary_module

    itinerary_module.fetch_itinerary = counting

    result = client.post(
        "/api/v1/road-trip/destinations/bulk",
        headers=auth_headers,
        json={
            "destinations": [
                {"name": "Marceline", **MARCELINE},
                {"name": "Jefferson City", **JEFFERSON_CITY},
            ]
        },
    )
    assert result.status_code == 201
    body = result.json()
    assert len(body["added"]) == 2
    assert body["skipped"] == []
    assert calls["n"] == 1


def test_adding_one_that_already_exists_is_reported_not_fatal(client, auth_headers, trip_answers):
    _home_with_coordinates(client, auth_headers)
    trip_answers(_trip(LEG_ONE))
    _add(client, auth_headers, "Marceline", **MARCELINE)

    result = client.post(
        "/api/v1/road-trip/destinations/bulk",
        headers=auth_headers,
        json={"destinations": [{"name": "Marceline", **MARCELINE}]},
    ).json()
    assert result["added"] == []
    assert result["skipped"][0]["reason"] == "Already on the list."


def test_a_place_no_road_reaches_does_not_take_the_others_with_it(
    client, auth_headers, trip_answers
):
    """OSRM is asked for one route through every waypoint, so an
    unreachable place fails the whole trip. The good ones are kept and the
    bad one is reported."""
    _home_with_coordinates(client, auth_headers)
    trip_answers(RoutingError("NoRoute — no road reaches Pearl City, HI"))

    result = client.post(
        "/api/v1/road-trip/destinations/bulk",
        headers=auth_headers,
        json={
            "destinations": [
                {"name": "Pearl City", "latitude": 21.4, "longitude": -157.97},
                {"name": "all the way to the Moon", "miles": 238900.0},
            ]
        },
    ).json()

    assert [d["name"] for d in result["added"]] == ["all the way to the Moon"]
    assert [s["name"] for s in result["skipped"]] == ["Pearl City"]
    assert "NoRoute" in result["skipped"][0]["reason"]


# --- searching for a place ---------------------------------------------


def test_a_town_can_be_searched_for_by_name(client, auth_headers):
    _home_with_coordinates(client, auth_headers)

    found = client.get("/api/v1/road-trip/places/search?q=marceline", headers=auth_headers).json()
    assert found[0]["short_name"] == "Marceline, MO"
    assert found[0]["name"] == "Brookfield R-III to Marceline, MO"
    assert found[0]["straight_line_miles"] < 15


def test_searching_works_before_a_home_is_set(client, auth_headers):
    """A district setting itself up needs to find its own town first."""
    found = client.get("/api/v1/road-trip/places/search?q=brookfield", headers=auth_headers).json()
    assert found
    assert found[0]["name"] == found[0]["short_name"]


def test_a_searched_place_can_be_added_as_a_waypoint(client, auth_headers, trip_answers):
    """The result feeds the ordinary create — search writes nothing
    itself."""
    _home_with_coordinates(client, auth_headers)
    found = client.get("/api/v1/road-trip/places/search?q=marceline", headers=auth_headers).json()[
        0
    ]

    trip_answers(_trip(LEG_ONE))
    created = client.post(
        "/api/v1/road-trip/destinations",
        headers=auth_headers,
        json={
            "name": found["name"],
            "short_name": found["short_name"],
            "latitude": found["latitude"],
            "longitude": found["longitude"],
        },
    )
    assert created.status_code == 201
    assert created.json()["miles"] == 12.1


def test_searching_writes_nothing(client, auth_headers):
    _home_with_coordinates(client, auth_headers)
    client.get("/api/v1/road-trip/places/search?q=columbia", headers=auth_headers)

    assert client.get("/api/v1/road-trip/destinations", headers=auth_headers).json() == []


def test_a_one_letter_search_is_refused(client, auth_headers):
    response = client.get("/api/v1/road-trip/places/search?q=k", headers=auth_headers)
    assert response.status_code == 422


def test_search_is_admin_only(client):
    assert client.get("/api/v1/road-trip/places/search?q=columbia").status_code == 401
