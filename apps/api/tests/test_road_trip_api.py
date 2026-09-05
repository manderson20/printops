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
from app.roadtrip.routing import DrivingRoute, RoutingError


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
JEFFERSON_CITY = {"latitude": 38.5767, "longitude": -92.1735}

# A stand-in for the road network. Real driving miles for the one journey
# these tests use, so an assertion that the drive beats the crow flies is
# checking arithmetic rather than a magic number.
FAKE_ROUTE = DrivingRoute(
    miles=122.6,
    geometry=[[39.7864, -93.0735], [39.2, -92.6], [38.5767, -92.1735]],
)


@pytest.fixture
def routing_answers(monkeypatch):
    """Swaps the routing call out. Nothing in this file touches a real
    routing service: what is worth testing is what PrintOps does with an
    answer, not whether somebody's demo server was up during CI."""

    def install(result):
        async def fake(**kwargs):
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr("app.routers.road_trip.fetch_driving_route", fake)

    return install


@pytest.fixture(autouse=True)
def routing_off_by_default(routing_answers):
    """Every test that does not say otherwise gets a routing service that
    cannot be reached, so a test which forgot to stub it fails loudly
    rather than reaching the internet from CI."""
    routing_answers(RoutingError("no routing service in tests"))


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
    assert (
        client.delete(f"/api/v1/road-trip/locations/{home['id']}", headers=auth_headers).status_code
        == 204
    )


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


# --- the drive, not the crow flies -------------------------------------


def _home_with_coordinates(client, auth_headers):
    return _make_location(
        client, auth_headers, name="Brookfield R-III", is_home=True, **BROOKFIELD
    ).json()


def test_a_destination_with_coordinates_gets_its_distance_measured(
    client, auth_headers, routing_answers
):
    """The point of the whole thing: nobody types 122.6."""
    _home_with_coordinates(client, auth_headers)
    routing_answers(FAKE_ROUTE)

    created = client.post(
        "/api/v1/road-trip/destinations",
        headers=auth_headers,
        json={"name": "Brookfield to Jefferson City", **JEFFERSON_CITY},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["miles"] == 122.6
    assert body["route_miles"] == 122.6
    assert body["has_route"] is True
    assert body["route_error"] is None
    # And it beats the straight line, which is the fact that made the old
    # map wrong.
    assert body["miles"] > body["straight_line_miles"]


def test_a_typed_distance_is_not_overwritten_by_the_route(client, auth_headers, routing_answers):
    """An admin who types 120 because that is what the road signs say
    keeps 120."""
    _home_with_coordinates(client, auth_headers)
    routing_answers(FAKE_ROUTE)

    body = client.post(
        "/api/v1/road-trip/destinations",
        headers=auth_headers,
        json={"name": "Brookfield to Jefferson City", "miles": 120.0, **JEFFERSON_CITY},
    ).json()
    assert body["miles"] == 120.0
    assert body["route_miles"] == 122.6


def test_a_destination_with_no_coordinates_needs_a_distance(client, auth_headers):
    """ "all the way to the Moon" is a distance and nothing else."""
    created = client.post(
        "/api/v1/road-trip/destinations",
        headers=auth_headers,
        json={"name": "all the way to the Moon", "miles": 238900.0},
    )
    assert created.status_code == 201
    assert created.json()["has_route"] is False


def test_a_destination_with_neither_is_refused(client, auth_headers):
    response = client.post(
        "/api/v1/road-trip/destinations", headers=auth_headers, json={"name": "nowhere"}
    )
    assert response.status_code == 422


def test_coordinates_with_no_reachable_route_and_no_distance_is_refused(
    client, auth_headers, routing_answers
):
    """Rather than quietly storing the straight line — a crow-flies number
    inside a sentence that claims a drive."""
    _home_with_coordinates(client, auth_headers)
    routing_answers(RoutingError("Could not reach the routing service: timeout"))

    response = client.post(
        "/api/v1/road-trip/destinations",
        headers=auth_headers,
        json={"name": "Brookfield to Jefferson City", **JEFFERSON_CITY},
    )
    assert response.status_code == 422
    assert "Couldn't measure the drive" in response.json()["detail"]


def test_a_route_can_be_fetched_again_later(client, auth_headers, routing_answers):
    """The nine seeded rungs all start with an estimate, because the
    migration deliberately makes no HTTP calls."""
    _home_with_coordinates(client, auth_headers)
    estimate = client.post(
        "/api/v1/road-trip/destinations",
        headers=auth_headers,
        json={"name": "Brookfield to Jefferson City", "miles": 120.0, **JEFFERSON_CITY},
    ).json()
    assert estimate["has_route"] is False
    assert estimate["route_error"] is not None

    routing_answers(FAKE_ROUTE)
    refreshed = client.post(
        f"/api/v1/road-trip/destinations/{estimate['id']}/route", headers=auth_headers
    ).json()
    assert refreshed["miles"] == 122.6
    assert refreshed["has_route"] is True
    assert refreshed["route_error"] is None


def test_refetching_a_rung_with_no_coordinates_is_refused(client, auth_headers):
    moon = _make_destination(client, auth_headers, name="the Moon", miles=238900.0).json()
    response = client.post(
        f"/api/v1/road-trip/destinations/{moon['id']}/route", headers=auth_headers
    )
    assert response.status_code == 422


def test_routing_can_be_switched_off(client, auth_headers, routing_answers):
    """For an installation with no outbound internet, every save pausing
    twenty seconds to fail is worse than not trying."""
    _home_with_coordinates(client, auth_headers)
    routing_answers(FAKE_ROUTE)
    client.put("/api/v1/road-trip/settings", headers=auth_headers, json={"routing_enabled": False})

    body = client.post(
        "/api/v1/road-trip/destinations",
        headers=auth_headers,
        json={"name": "Brookfield to Jefferson City", "miles": 120.0, **JEFFERSON_CITY},
    ).json()
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


def test_several_destinations_can_be_added_at_once(client, auth_headers, routing_answers):
    _home_with_coordinates(client, auth_headers)
    routing_answers(FAKE_ROUTE)

    result = client.post(
        "/api/v1/road-trip/destinations/bulk",
        headers=auth_headers,
        json={
            "destinations": [
                {"name": "Brookfield to Jefferson City", **JEFFERSON_CITY},
                {"name": "Brookfield to Kirksville", "latitude": 40.1948, "longitude": -92.5832},
            ]
        },
    )
    assert result.status_code == 201
    body = result.json()
    assert len(body["added"]) == 2
    assert body["skipped"] == []


def test_one_unroutable_place_does_not_lose_the_others(client, auth_headers, routing_answers):
    """Six routes fetched from a service that can time out on any of them
    must not mean six lost when one fails."""
    _home_with_coordinates(client, auth_headers)

    calls = {"n": 0}

    async def sometimes(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RoutingError("NoRoute — no road reaches Pearl City, HI")
        return FAKE_ROUTE

    import app.routers.road_trip as road_trip_router

    road_trip_router.fetch_driving_route = sometimes

    result = client.post(
        "/api/v1/road-trip/destinations/bulk",
        headers=auth_headers,
        json={
            "destinations": [
                {"name": "Brookfield to Pearl City, HI", "latitude": 21.4, "longitude": -157.97},
                {"name": "Brookfield to Jefferson City", **JEFFERSON_CITY},
            ]
        },
    ).json()

    assert [d["name"] for d in result["added"]] == ["Brookfield to Jefferson City"]
    assert [s["name"] for s in result["skipped"]] == ["Brookfield to Pearl City, HI"]
    assert "NoRoute" in result["skipped"][0]["reason"]


def test_adding_one_that_already_exists_is_reported_not_fatal(
    client, auth_headers, routing_answers
):
    _home_with_coordinates(client, auth_headers)
    routing_answers(FAKE_ROUTE)
    client.post(
        "/api/v1/road-trip/destinations",
        headers=auth_headers,
        json={"name": "Brookfield to Jefferson City", **JEFFERSON_CITY},
    )

    result = client.post(
        "/api/v1/road-trip/destinations/bulk",
        headers=auth_headers,
        json={"destinations": [{"name": "Brookfield to Jefferson City", **JEFFERSON_CITY}]},
    ).json()
    assert result["added"] == []
    assert result["skipped"][0]["reason"] == "Already on the list."
