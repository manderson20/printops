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
