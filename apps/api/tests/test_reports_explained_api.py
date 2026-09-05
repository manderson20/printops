"""API tests for the three "Your Printing, Explained" endpoints.

The anonymity rule on /explained/district is the reason this file exists.
It is asserted three ways, because each catches a different mistake:
what the payload *contains* (no addresses, no place names), what it
*totals* (everybody's pages, not the caller's — proof the endpoint really
did bypass _report_filters), and what it *withholds* (nothing at all when
too few people contributed).
"""

import uuid
from datetime import UTC, datetime, time

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db import get_db
from app.main import app
from app.models.base import Base
from app.models.copier_usage import CopierUsageRecord
from app.models.destination import Destination
from app.models.job import Job
from app.models.location import Location
from app.models.mfp_device import MfpDevice
from app.models.printer import Printer
from app.reports.equivalency import resolve_period
from app.reports.equivalency_config import MIN_CONTRIBUTORS_FOR_DISTRICT_FACTS

EXPLAINED = "/api/v1/reports/explained"

GOOGLE_CLAIMS = {
    "sub": "google-sub-viewer",
    "email": "viewer@example.org",
    "email_verified": True,
    "hd": "example.org",
    "name": "Viewer Person",
    "picture": None,
}

# Deliberately distinctive so a leak is unmistakable in an assertion
# failure, and so no substring of them occurs naturally in a fun fact.
BUILDING = "Zebra Heights Academy"
DEPARTMENT = "Quokka Studies"


@pytest_asyncio.fixture
async def db_session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
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
def backend_headers():
    return {"X-Backend-Token": get_settings().backend_token}


@pytest.fixture
def admin_headers(client):
    response = client.post("/auth/login", json={"username": "admin", "password": "changeme"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def google_settings(client, admin_headers):
    response = client.put(
        "/api/v1/settings/google-sso",
        headers=admin_headers,
        json={
            "client_id": "test-client-id",
            "client_secret": "test-client-secret",
            "workspace_domain": "example.org",
            "initial_admin_emails": [],
            "redirect_base_url": "https://printops.test",
            "enabled": True,
        },
    )
    assert response.status_code == 200
    return response.json()


@pytest.fixture
def viewer_headers(client, google_settings, monkeypatch):
    async def fake_exchange_code(**kwargs):
        return {"id_token": "fake-id-token"}

    def fake_verify_id_token(id_token, client_id):
        return GOOGLE_CLAIMS

    monkeypatch.setattr("app.routers.auth.exchange_code", fake_exchange_code)
    monkeypatch.setattr("app.routers.auth.verify_id_token", fake_verify_id_token)

    login_response = client.get("/auth/google/login", follow_redirects=False)
    state = login_response.cookies["printops_oauth_state"]
    response = client.get(
        "/auth/google/callback",
        params={"code": "fake-code", "state": state},
        cookies={"printops_oauth_state": state},
        follow_redirects=False,
    )
    token = response.headers["location"].split("token=", 1)[1]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def ou_viewer_headers(client, admin_headers):
    """An ou_viewer token, obtained by pre-provisioning the account and
    impersonating it — the only way to get a token carrying that role
    without a real Google sign-in."""
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={
            "email": "ou-viewer@example.org",
            "role": "ou_viewer",
            "granted_ou_paths": ["/Employees"],
        },
    )
    assert created.status_code == 201, created.text
    token = client.post(
        f"/api/v1/users/{created.json()['id']}/impersonate", headers=admin_headers
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def printer_id(db_session_factory):
    async with db_session_factory() as session:
        printer = Printer(
            name="Zebra Printer",
            ip_address="10.0.0.9",
            building=BUILDING,
            department=DEPARTMENT,
        )
        session.add(printer)
        await session.commit()
        await session.refresh(printer)
        return str(printer.id)


@pytest_asyncio.fixture
async def mfp_device_id(db_session_factory):
    async with db_session_factory() as session:
        device = MfpDevice(
            name="Zebra Copier",
            vendor="konica",
            connector_type="konica_bizhub",
            building=BUILDING,
        )
        session.add(device)
        await session.commit()
        await session.refresh(device)
        return str(device.id)


def _in_window() -> datetime:
    """A naive-UTC instant an hour into the current school-year window.

    Rows are given an explicit created_at rather than letting
    `server_default=func.now()` fill it in, because SQLite has no real
    timestamptz. It stores that default as a naive string
    ("2026-09-02 00:48:46") and the reports compare it against a
    timezone-aware bound ("2026-09-02 00:00:00-05:00"), which SQLite
    resolves as a *string* comparison — where the "-05:00" suffix sorts
    after the seconds field and pushes a just-written row outside a
    window that genuinely contains it.

    Postgres compares the two as real instants and is unaffected
    (verified against production), so this is a harness artifact rather
    than a product bug and the workaround belongs here rather than in the
    query. An hour past the window start is far enough from both bounds
    to compare correctly as a string on any date of the year.
    """
    start, _ = resolve_period("year", datetime.now(UTC).date())
    return datetime.combine(start, time(1, 0))


async def _make_job(
    db_session_factory,
    printer_id,
    submitted_by,
    page_count,
    duplex=None,
    color_mode=None,
):
    async with db_session_factory() as session:
        session.add(
            Job(
                printer_id=uuid.UUID(printer_id),
                submitted_by=submitted_by,
                status="forwarded",
                page_count=page_count,
                duplex=duplex,
                color_mode=color_mode,
                created_at=_in_window(),
            )
        )
        await session.commit()


async def _make_copy(db_session_factory, device_id, staff_email, page_count):
    async with db_session_factory() as session:
        session.add(
            CopierUsageRecord(
                mfp_device_id=uuid.UUID(device_id),
                vendor="konica",
                location_building=BUILDING,
                staff_email=staff_email,
                external_identity_used=staff_email or "unknown",
                source_connector="konica_bizhub",
                activity_type="copy",
                page_count=page_count,
                # Counter-derived: a period, never an instant.
                period_start=datetime.now(UTC),
                period_end=datetime.now(UTC),
                created_at=_in_window(),
                raw_payload={},
            )
        )
        await session.commit()


async def _seed_a_crowd(db_session_factory, printer_id, count=None):
    """Enough distinct people to clear the anonymity floor, since below it
    the district endpoint correctly returns nothing to assert on."""
    count = count or MIN_CONTRIBUTORS_FOR_DISTRICT_FACTS + 2
    for index in range(count):
        await _make_job(db_session_factory, printer_id, f"person{index}@example.org", 10)
    return count


# --- authentication ----------------------------------------------------


@pytest.mark.parametrize("path", ["/me", "/district", "/district/detail"])
def test_every_explained_endpoint_requires_auth(client, path):
    assert client.get(f"{EXPLAINED}{path}").status_code == 401


# --- role boundaries ---------------------------------------------------


def test_district_fun_facts_are_open_to_a_plain_viewer(client, viewer_headers):
    assert client.get(f"{EXPLAINED}/district", headers=viewer_headers).status_code == 200


def test_district_fun_facts_are_open_to_an_ou_viewer(client, ou_viewer_headers):
    assert client.get(f"{EXPLAINED}/district", headers=ou_viewer_headers).status_code == 200


def test_detail_is_refused_to_a_viewer(client, viewer_headers):
    assert client.get(f"{EXPLAINED}/district/detail", headers=viewer_headers).status_code == 403


def test_detail_is_refused_to_an_ou_viewer(client, ou_viewer_headers):
    """An ou_viewer is scoped to a roster, not trusted with the district
    breakdown — the building split is exactly what their scope excludes."""
    assert client.get(f"{EXPLAINED}/district/detail", headers=ou_viewer_headers).status_code == 403


def test_detail_is_allowed_for_an_admin(client, admin_headers):
    assert client.get(f"{EXPLAINED}/district/detail", headers=admin_headers).status_code == 200


# --- the anonymity rule ------------------------------------------------


async def test_district_payload_carries_no_identifying_strings(
    client, printer_id, db_session_factory, viewer_headers
):
    """The payload is searched as raw text, so a leak through a field
    nobody thought about still fails this."""
    await _seed_a_crowd(db_session_factory, printer_id)

    response = client.get(f"{EXPLAINED}/district", headers=viewer_headers)
    assert response.status_code == 200
    raw = response.text

    assert "@" not in raw
    assert BUILDING not in raw
    assert DEPARTMENT not in raw
    assert "example.org" not in raw


async def test_district_response_has_no_field_that_could_carry_a_person(
    client, printer_id, db_session_factory, viewer_headers
):
    """The type is the guard, so the shape is asserted directly rather
    than only its current contents."""
    await _seed_a_crowd(db_session_factory, printer_id)

    body = client.get(f"{EXPLAINED}/district", headers=viewer_headers).json()
    assert set(body) == {
        "period",
        "range_start",
        "range_end",
        "print_pages",
        "copy_pages",
        "total_pages",
        "sheets",
        "contributors",
        "has_enough_activity",
        "equivalencies",
        "facts",
        # Configuration an admin typed in Settings — the same home and the
        # same destinations for every viewer and every period — plus a
        # mileage that is `total_pages` in different units. See
        # DistrictFunFactsOut's docstring on why this does not weaken the
        # rule, and test_the_route_carries_configuration_and_no_usage below.
        "route",
    }


async def _seed_a_road_trip(db_session_factory):
    """A home and one reachable place, so the district page has a map."""
    async with db_session_factory() as session:
        session.add(
            Location(
                name="District Office",
                latitude=39.7864,
                longitude=-93.0735,
                is_home=True,
            )
        )
        session.add(
            Destination(
                name="Brookfield to Marceline",
                short_name="Marceline",
                miles=12.0,
                latitude=39.7117,
                longitude=-92.9477,
            )
        )
        await session.commit()


async def test_there_is_no_route_until_a_district_configures_one(
    client, printer_id, db_session_factory, viewer_headers
):
    """A dashboard with no map, never a broken one."""
    await _seed_a_crowd(db_session_factory, printer_id)

    body = client.get(f"{EXPLAINED}/district", headers=viewer_headers).json()
    assert body["route"] is None


async def test_the_route_is_drawn_once_a_home_and_a_destination_exist(
    client, printer_id, db_session_factory, viewer_headers
):
    await _seed_a_crowd(db_session_factory, printer_id)
    await _seed_a_road_trip(db_session_factory)

    route = client.get(f"{EXPLAINED}/district", headers=viewer_headers).json()["route"]
    assert route["home_name"] == "District Office"
    assert [stop["label"] for stop in route["stops"]] == ["Marceline"]
    assert route["miles_travelled"] >= 0


async def test_the_route_carries_configuration_and_no_usage(
    client, printer_id, db_session_factory, viewer_headers
):
    """Every field of `route` is either something an admin typed or a unit
    conversion of a total already in the response. Nothing in it varies
    with who printed, so nothing in it can be a segment of the district's
    printing."""
    await _seed_a_crowd(db_session_factory, printer_id)
    await _seed_a_road_trip(db_session_factory)

    body = client.get(f"{EXPLAINED}/district", headers=viewer_headers).json()
    route = body["route"]
    assert set(route) == {
        "home_name",
        "home_latitude",
        "home_longitude",
        "miles_travelled",
        "stops",
        "position",
    }
    assert set(route["stops"][0]) == {
        "name",
        "label",
        "miles",
        "latitude",
        "longitude",
        "reached",
    }
    assert BUILDING not in client.get(f"{EXPLAINED}/district", headers=viewer_headers).text


async def test_district_totals_are_everyones_not_the_callers(
    client, printer_id, db_session_factory, viewer_headers, admin_headers
):
    """The behavioural proof that _report_filters was bypassed.

    A viewer sees their own data everywhere else in the reports API. If
    this endpoint ever picks that dependency back up, the viewer's total
    silently collapses to their own handful of pages and every fun fact
    on the shared screen becomes a private one — so the viewer's answer
    is asserted equal to the admin's, not merely non-zero.
    """
    await _seed_a_crowd(db_session_factory, printer_id, count=12)
    await _make_job(db_session_factory, printer_id, "viewer@example.org", 7)

    viewer_body = client.get(f"{EXPLAINED}/district", headers=viewer_headers).json()
    admin_body = client.get(f"{EXPLAINED}/district", headers=admin_headers).json()

    assert viewer_body["total_pages"] == admin_body["total_pages"]
    assert viewer_body["total_pages"] == 12 * 10 + 7
    assert viewer_body["contributors"] == 13


async def test_district_withholds_totals_below_the_anonymity_floor(
    client, printer_id, db_session_factory, viewer_headers
):
    """Two people sharing a total is not anonymous — either can subtract
    their own and read the other's."""
    await _make_job(db_session_factory, printer_id, "alice@example.org", 40)
    await _make_job(db_session_factory, printer_id, "bob@example.org", 60)

    body = client.get(f"{EXPLAINED}/district", headers=viewer_headers).json()
    assert body["has_enough_activity"] is False
    assert body["contributors"] == 2
    assert body["total_pages"] == 0
    assert body["print_pages"] == 0
    assert body["sheets"] == 0
    assert body["facts"] == []
    assert body["equivalencies"] == []


async def test_district_starts_reporting_exactly_at_the_floor(
    client, printer_id, db_session_factory, viewer_headers
):
    await _seed_a_crowd(db_session_factory, printer_id, count=MIN_CONTRIBUTORS_FOR_DISTRICT_FACTS)

    body = client.get(f"{EXPLAINED}/district", headers=viewer_headers).json()
    assert body["contributors"] == MIN_CONTRIBUTORS_FOR_DISTRICT_FACTS
    assert body["has_enough_activity"] is True
    assert body["total_pages"] > 0


# --- personal scoping --------------------------------------------------


async def test_personal_view_shows_only_the_caller(
    client, printer_id, db_session_factory, viewer_headers
):
    await _make_job(db_session_factory, printer_id, "viewer@example.org", 10)
    await _make_job(db_session_factory, printer_id, "someone.else@example.org", 500)

    body = client.get(f"{EXPLAINED}/me", headers=viewer_headers).json()
    assert body["print_pages"] == 10
    assert body["job_count"] == 1


async def test_personal_view_scopes_an_admin_to_themselves_too(
    client, printer_id, db_session_factory, admin_headers
):
    """The bug this endpoint exists to avoid: _report_filters leaves an
    admin unscoped, which would hand them the whole district under a
    heading saying "you"."""
    await _make_job(db_session_factory, printer_id, "someone.else@example.org", 500)

    body = client.get(f"{EXPLAINED}/me", headers=admin_headers).json()
    assert body["print_pages"] == 0


async def test_personal_view_compares_against_the_district_without_exposing_it(
    client, printer_id, db_session_factory, viewer_headers
):
    """The median is computed from everybody, but only the median comes
    back — no addresses, no per-person rows."""
    for index in range(4):
        await _make_job(db_session_factory, printer_id, f"person{index}@example.org", 100)
    await _make_job(db_session_factory, printer_id, "viewer@example.org", 200)

    response = client.get(f"{EXPLAINED}/me", headers=viewer_headers)
    body = response.json()
    assert body["district_median_pages"] == 100.0
    assert body["times_district_median"] == 2.0
    assert "person0@example.org" not in response.text


def test_personal_view_reports_no_multiple_when_the_median_is_zero(client, viewer_headers):
    body = client.get(f"{EXPLAINED}/me", headers=viewer_headers).json()
    assert body["district_median_pages"] == 0.0
    assert body["times_district_median"] is None


async def test_duplex_opportunity_is_offered_not_charged(
    client, printer_id, db_session_factory, viewer_headers
):
    await _make_job(db_session_factory, printer_id, "viewer@example.org", 100, duplex=False)

    body = client.get(f"{EXPLAINED}/me", headers=viewer_headers).json()
    assert body["additional_sheets_if_all_duplex"] == 50
    assert body["duplex_sheets_saved"] == 0
    assert any("would save" in fact for fact in body["facts"])
    for judgement in ("wasted", "too much", "should have"):
        assert all(judgement not in fact.lower() for fact in body["facts"])


# --- copies are included -----------------------------------------------


async def test_copies_count_toward_the_district_total(
    client, printer_id, mfp_device_id, backend_headers, db_session_factory, viewer_headers
):
    await _seed_a_crowd(db_session_factory, printer_id, count=10)
    await _make_copy(db_session_factory, mfp_device_id, "copier.user@example.org", 250)

    body = client.get(f"{EXPLAINED}/district", headers=viewer_headers).json()
    assert body["print_pages"] == 100
    assert body["copy_pages"] == 250
    assert body["total_pages"] == 350
    # The copier user is a contributor too, without having printed.
    assert body["contributors"] == 11


async def test_personal_view_flags_period_derived_copies(
    client, mfp_device_id, db_session_factory, viewer_headers
):
    """Copies come from counter deltas covering a window, so the page must
    say time-of-day figures aren't available rather than quietly showing
    printing alone."""
    await _make_copy(db_session_factory, mfp_device_id, "viewer@example.org", 30)

    body = client.get(f"{EXPLAINED}/me", headers=viewer_headers).json()
    assert body["copy_pages"] == 30
    assert body["includes_period_derived_copies"] is True
    assert body["time_of_day_available"] is False


async def test_time_of_day_is_available_when_nothing_was_copied(
    client, printer_id, db_session_factory, viewer_headers
):
    await _make_job(db_session_factory, printer_id, "viewer@example.org", 10)

    body = client.get(f"{EXPLAINED}/me", headers=viewer_headers).json()
    assert body["includes_period_derived_copies"] is False
    assert body["time_of_day_available"] is True


# --- the admin breakdown -----------------------------------------------


async def test_detail_splits_by_building_and_department(
    client, printer_id, db_session_factory, admin_headers
):
    await _seed_a_crowd(db_session_factory, printer_id, count=10)

    body = client.get(f"{EXPLAINED}/district/detail", headers=admin_headers).json()
    assert [s["label"] for s in body["by_building"]] == [BUILDING]
    assert [s["label"] for s in body["by_department"]] == [DEPARTMENT]
    assert body["by_building"][0]["total_pages"] == 100


async def test_detail_building_rows_reconcile_with_the_district_total(
    client, printer_id, db_session_factory, admin_headers
):
    """The Unassigned row is computed by subtraction precisely so this
    holds — a breakdown whose columns don't add up is worse than none."""
    await _seed_a_crowd(db_session_factory, printer_id, count=10)

    body = client.get(f"{EXPLAINED}/district/detail", headers=admin_headers).json()
    assert sum(s["print_pages"] for s in body["by_building"]) == body["print_pages"]
    assert sum(s["total_pages"] for s in body["by_building"]) == body["total_pages"]


async def test_detail_shows_activity_with_no_building_as_unassigned(
    client, backend_headers, admin_headers, db_session_factory
):
    async with db_session_factory() as session:
        printer = Printer(name="Homeless Printer", ip_address="10.0.0.11")
        session.add(printer)
        await session.commit()
        await session.refresh(printer)
        nameless_printer_id = str(printer.id)

    for index in range(10):
        await _make_job(db_session_factory, nameless_printer_id, f"person{index}@example.org", 10)

    body = client.get(f"{EXPLAINED}/district/detail", headers=admin_headers).json()
    unassigned = [s for s in body["by_building"] if s["key"] == "__unassigned__"]
    assert len(unassigned) == 1
    assert unassigned[0]["total_pages"] == 100
    # Contributor counts overlap across buildings, so this row can't
    # derive one and says so with a zero the client renders as a dash.
    assert unassigned[0]["people"] == 0


def test_detail_omits_a_building_with_no_activity(
    client, printer_id, backend_headers, admin_headers
):
    """The fixture's printer exists but nobody used it — an empty row is
    noise on a breakdown."""
    body = client.get(f"{EXPLAINED}/district/detail", headers=admin_headers).json()
    assert body["by_building"] == []


# --- periods -----------------------------------------------------------


@pytest.mark.parametrize("period", ["week", "month", "semester", "year"])
def test_every_named_period_is_accepted(client, admin_headers, period):
    response = client.get(f"{EXPLAINED}/district", params={"period": period}, headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["period"] == period


def test_an_unknown_period_is_refused_rather_than_guessed_at(client, admin_headers):
    response = client.get(
        f"{EXPLAINED}/district", params={"period": "fortnight"}, headers=admin_headers
    )
    assert response.status_code == 422
    assert "fortnight" in response.json()["detail"]


def test_the_reported_window_matches_what_was_asked_for(client, admin_headers):
    body = client.get(f"{EXPLAINED}/me", params={"period": "month"}, headers=admin_headers).json()
    assert body["range_start"].endswith("-01")
    assert body["period"] == "month"


# --- equivalencies in the payload --------------------------------------


async def test_equivalencies_carry_milestones_only_where_a_ladder_applies(
    client, printer_id, db_session_factory, admin_headers
):
    # A realistic volume, so every fact clears the trivial threshold —
    # see the test below for what happens when they don't.
    for index in range(10):
        await _make_job(db_session_factory, printer_id, f"person{index}@example.org", 500)

    body = client.get(f"{EXPLAINED}/district", headers=admin_headers).json()
    by_key = {e["key"]: e for e in body["equivalencies"]}
    assert by_key["distance"]["milestone"] is not None
    assert by_key["distance"]["milestone"]["upcoming"]["label"]
    assert by_key["trees"]["milestone"] is None


async def test_a_fact_that_would_round_to_zero_is_dropped_not_shown(
    client, printer_id, db_session_factory, admin_headers
):
    """100 sheets is 0.012 of a tree. "You used 0.0 trees" is noise, so
    the fact is omitted — while the ones that are still real at that
    scale stay."""
    await _seed_a_crowd(db_session_factory, printer_id, count=10)

    body = client.get(f"{EXPLAINED}/district", headers=admin_headers).json()
    keys = {e["key"] for e in body["equivalencies"]}
    assert "trees" not in keys
    assert "water" in keys
    assert "distance" in keys


def test_a_period_with_no_activity_produces_no_equivalencies(client, admin_headers):
    body = client.get(f"{EXPLAINED}/me", headers=admin_headers).json()
    assert body["total_pages"] == 0
    assert body["equivalencies"] == []
    assert body["facts"] == []


# --- my activity: line items -------------------------------------------


async def test_activity_lists_a_print_with_an_instant_not_a_window(
    client, printer_id, db_session_factory, viewer_headers
):
    await _make_job(db_session_factory, printer_id, "viewer@example.org", 12)

    body = client.get(f"{EXPLAINED}/me/activity", headers=viewer_headers).json()
    assert body["total_rows"] == 1
    row = body["rows"][0]
    assert row["kind"] == "print"
    assert row["activity_type"] == "print"
    assert row["pages"] == 12
    assert row["at"] is not None
    # A print is a moment; it must not claim a window it never had.
    assert row["window_start"] is None
    assert row["window_end"] is None


async def test_activity_lists_a_copy_with_a_window_not_an_instant(
    client, mfp_device_id, db_session_factory, viewer_headers
):
    """The whole reason this list exists in two shapes. A copy row that
    carried a timestamp would be stating something nobody measured."""
    await _make_copy(db_session_factory, mfp_device_id, "viewer@example.org", 47)

    body = client.get(f"{EXPLAINED}/me/activity", headers=viewer_headers).json()
    row = body["rows"][0]
    assert row["kind"] == "copy"
    assert row["pages"] == 47
    assert row["at"] is None
    assert row["window_start"] is not None
    assert row["window_end"] is not None
    assert body["includes_period_derived_copies"] is True


async def test_activity_gives_one_row_per_counter_delta(
    client, mfp_device_id, db_session_factory, viewer_headers
):
    """Per-delta, not rolled up per day — rolling up would discard the
    only timing information the hardware produces."""
    for pages in (10, 20, 30):
        await _make_copy(db_session_factory, mfp_device_id, "viewer@example.org", pages)

    body = client.get(f"{EXPLAINED}/me/activity", headers=viewer_headers).json()
    assert body["total_rows"] == 3
    assert sorted(row["pages"] for row in body["rows"]) == [10, 20, 30]


async def test_activity_merges_both_sources_newest_first(
    client, printer_id, mfp_device_id, db_session_factory, viewer_headers
):
    await _make_job(db_session_factory, printer_id, "viewer@example.org", 5)
    await _make_copy(db_session_factory, mfp_device_id, "viewer@example.org", 9)

    body = client.get(f"{EXPLAINED}/me/activity", headers=viewer_headers).json()
    assert body["total_rows"] == 2
    assert {row["kind"] for row in body["rows"]} == {"print", "copy"}
    keys = [row["at"] or row["window_end"] for row in body["rows"]]
    assert keys == sorted(keys, reverse=True)


async def test_activity_shows_only_the_callers_own_rows(
    client, printer_id, mfp_device_id, db_session_factory, viewer_headers
):
    await _make_job(db_session_factory, printer_id, "viewer@example.org", 5)
    await _make_job(db_session_factory, printer_id, "someone.else@example.org", 500)
    await _make_copy(db_session_factory, mfp_device_id, "someone.else@example.org", 900)

    response = client.get(f"{EXPLAINED}/me/activity", headers=viewer_headers)
    assert response.json()["total_rows"] == 1
    assert "someone.else@example.org" not in response.text


async def test_activity_scopes_an_admin_to_themselves_too(
    client, printer_id, db_session_factory, admin_headers
):
    await _make_job(db_session_factory, printer_id, "someone.else@example.org", 500)

    body = client.get(f"{EXPLAINED}/me/activity", headers=admin_headers).json()
    assert body["total_rows"] == 0


async def test_activity_reports_the_true_total_when_capped(
    client, printer_id, db_session_factory, viewer_headers
):
    """A page must be able to say "showing 2 of 5" rather than presenting
    a slice as the whole history."""
    for index in range(5):
        await _make_job(db_session_factory, printer_id, "viewer@example.org", index + 1)

    body = client.get(
        f"{EXPLAINED}/me/activity", params={"limit": 2}, headers=viewer_headers
    ).json()
    assert len(body["rows"]) == 2
    assert body["total_rows"] == 5


async def test_activity_names_the_document_and_the_machine(
    client, printer_id, mfp_device_id, db_session_factory, viewer_headers
):
    await _make_job(db_session_factory, printer_id, "viewer@example.org", 3)
    await _make_copy(db_session_factory, mfp_device_id, "viewer@example.org", 4)

    rows = {
        r["kind"]: r
        for r in client.get(f"{EXPLAINED}/me/activity", headers=viewer_headers).json()["rows"]
    }
    # A job created without a document name still gets something to show.
    assert rows["print"]["label"] == "Untitled document"
    assert rows["print"]["where"] == "Zebra Printer"
    # A counter delta has no document, so the copier names the row.
    assert rows["copy"]["label"] == "Zebra Copier"
    assert rows["copy"]["where"] == "Zebra Copier"


async def test_activity_is_empty_and_honest_with_no_copies(
    client, printer_id, db_session_factory, viewer_headers
):
    await _make_job(db_session_factory, printer_id, "viewer@example.org", 3)

    body = client.get(f"{EXPLAINED}/me/activity", headers=viewer_headers).json()
    assert body["includes_period_derived_copies"] is False


async def test_activity_requires_auth(client):
    assert client.get(f"{EXPLAINED}/me/activity").status_code == 401
