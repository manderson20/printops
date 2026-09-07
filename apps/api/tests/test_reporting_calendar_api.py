"""The reporting calendar an organisation configures, over HTTP.

`tests/test_reporting_periods.py` covers the resolution rules. This file covers
what a client can actually do with them: read the periods to offer, save a
calendar, and be refused when the calendar would not resolve.

The case worth naming is the last one. A calendar is four small numbers and a
list, and every wrong combination of them fails quietly — a term dated outside
its year, two terms starting on the same day, 31 February. None of those raise
anything visible at save time; they surface weeks later as a report that looks
slightly low, which nobody attributes to a settings page.
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.base import Base

CALENDAR = "/api/v1/settings/reporting-calendar"
PERIODS = "/api/v1/reports/periods"

SCHOOL = {
    "year_start_month": 7,
    "year_start_day": 1,
    "year_noun": "School year",
    "label_style": "auto",
    "terms": [
        {"name": "Fall Semester", "start_month": 8, "start_day": 15},
        {"name": "Spring Semester", "start_month": 1, "start_day": 5},
    ],
}


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
def admin_headers(client):
    response = client.post("/auth/login", json={"username": "admin", "password": "changeme"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# --- defaults ---------------------------------------------------------------


def test_an_installation_that_has_configured_nothing_gets_a_neutral_calendar(client, admin_headers):
    """Not a school's. PrintOps is installed by organisations that are not
    schools, and a fresh install showing "School year" and "This semester" is
    wrong on its face for them."""
    body = client.get(CALENDAR, headers=admin_headers).json()

    assert body["year_noun"] == "Year"
    assert body["terms"] == [], "no terms until somebody says there are terms"


def test_the_period_picker_offers_no_term_when_there_are_none(client, admin_headers):
    """A "term" that silently resolved to the whole year would show the same
    span twice under two names, which reads as a bug to whoever sees it."""
    body = client.get(PERIODS, headers=admin_headers).json()
    kinds = [option["kind"] for option in body["options"]]

    assert "term" not in kinds
    assert kinds.count("year") == 1


# --- saving -----------------------------------------------------------------


def test_a_saved_calendar_renames_the_periods_everyone_sees(client, admin_headers):
    saved = client.put(CALENDAR, headers=admin_headers, json=SCHOOL)
    assert saved.status_code == 200

    body = client.get(PERIODS, headers=admin_headers).json()
    assert body["year_noun"] == "School year"

    labels = {option["kind"]: option["label"] for option in body["options"]}
    assert labels["year"].startswith("School year "), labels["year"]
    assert "–" in labels["year"], "a July-start year spans two, so it says both"


def test_positions_are_assigned_from_the_submitted_order(client, admin_headers):
    """`position` ties a term to its counterpart a year earlier, which is what
    "the same term last year" will be answered from. Taking it from the client
    would let two terms claim the same one and make that comparison silently
    pick whichever it found first."""
    body = client.put(CALENDAR, headers=admin_headers, json=SCHOOL).json()
    assert [term["position"] for term in body["terms"]] == [0, 1]


def test_saving_replaces_the_previous_terms_rather_than_adding_to_them(client, admin_headers):
    """The editor shows a year at once, so a save means "this is the year" —
    not "add these". Appending would leave a year with two Falls, and the
    resolver would report against whichever it sorted first."""
    client.put(CALENDAR, headers=admin_headers, json=SCHOOL)

    quarters = dict(SCHOOL)
    quarters["year_noun"] = "Fiscal year"
    quarters["terms"] = [
        {"name": "Q1", "start_month": 7, "start_day": 1},
        {"name": "Q2", "start_month": 10, "start_day": 1},
        {"name": "Q3", "start_month": 1, "start_day": 1},
        {"name": "Q4", "start_month": 4, "start_day": 1},
    ]
    body = client.put(CALENDAR, headers=admin_headers, json=quarters).json()

    assert [term["name"] for term in body["terms"]] == ["Q1", "Q2", "Q3", "Q4"]


def test_a_year_can_have_no_terms_at_all(client, admin_headers):
    """A supported shape. An organisation that does not subdivide its year is
    configured, not unconfigured."""
    none = dict(SCHOOL)
    none["terms"] = []
    body = client.put(CALENDAR, headers=admin_headers, json=none).json()
    assert body["terms"] == []


def test_the_response_previews_the_year_the_settings_produce(client, admin_headers):
    """So an admin reads back the year they described. A calendar that reads
    plausibly and resolves wrongly is the failure this is here to catch, and it
    is invisible without showing the dates."""
    body = client.put(CALENDAR, headers=admin_headers, json=SCHOOL).json()

    kinds = [period["kind"] for period in body["preview"]]
    assert kinds == ["year", "term", "term"]
    assert all(period["start"] < period["end"] for period in body["preview"])


# --- refusing what would not resolve ----------------------------------------


@pytest.mark.parametrize(
    ("change", "because"),
    [
        ({"year_start_month": 2, "year_start_day": 31}, "31 February is not a date"),
        (
            {
                "terms": [
                    {"name": "Fall", "start_month": 8, "start_day": 15},
                    {"name": "fall", "start_month": 1, "start_day": 5},
                ]
            },
            "two terms sharing a name cannot be told apart on a report",
        ),
        (
            {
                "terms": [
                    {"name": "Fall", "start_month": 8, "start_day": 15},
                    {"name": "Spring", "start_month": 8, "start_day": 15},
                ]
            },
            "two terms starting on the same day leaves one of them zero days long",
        ),
        ({"label_style": "whatever"}, "an unknown label style"),
        ({"year_noun": ""}, "a period has to be called something"),
    ],
)
def test_a_calendar_that_would_not_resolve_is_refused(client, admin_headers, change, because):
    payload = dict(SCHOOL)
    payload.update(change)

    response = client.put(CALENDAR, headers=admin_headers, json=payload)
    assert response.status_code == 422, because


def test_only_an_admin_can_change_the_calendar(client):
    """Reading is open — the picker is on screens viewers see. Writing is not:
    the calendar decides what every report on the system covers."""
    assert client.put(CALENDAR, json=SCHOOL).status_code in (401, 403)


# --- the old names keep working ---------------------------------------------


def test_an_existing_bookmark_still_resolves(client, admin_headers):
    """`semester` is what the web app sent for years and what people have
    bookmarked. It maps to the term containing today, whatever this
    organisation calls its terms — a bookmark must not 500 because somebody
    renamed Fall to Q1."""
    client.put(CALENDAR, headers=admin_headers, json=SCHOOL)

    response = client.get(
        "/api/v1/reports/explained/me", headers=admin_headers, params={"period": "semester"}
    )
    assert response.status_code == 200


def test_an_unknown_period_is_refused_rather_than_silently_empty(client, admin_headers):
    response = client.get(
        "/api/v1/reports/explained/me", headers=admin_headers, params={"period": "fortnight"}
    )
    assert response.status_code == 422


# --- the audit trail --------------------------------------------------------


def _calendar_events(client, admin_headers) -> list[dict]:
    response = client.get(
        "/api/v1/audit",
        headers=admin_headers,
        params={"action_prefix": "settings.reporting_calendar"},
    )
    assert response.status_code == 200, response.text
    return response.json()["events"]


def test_changing_only_the_terms_still_records_an_audit_event(client, admin_headers):
    """The change most worth a record, and the one that nearly had none.

    Terms live in their own table, so the usual settings diff — which compares
    a model's own columns — sees nothing when only they change, and writes no
    event at all. An admin could have redefined every term silently, which
    changes what every report ever run against that term covers.
    """
    client.put(CALENDAR, headers=admin_headers, json=SCHOOL)

    moved = dict(SCHOOL)
    moved["terms"] = [
        {"name": "Fall Semester", "start_month": 8, "start_day": 22},
        {"name": "Spring Semester", "start_month": 1, "start_day": 5},
    ]
    assert client.put(CALENDAR, headers=admin_headers, json=moved).status_code == 200

    rows = _calendar_events(client, admin_headers)
    assert rows, "a term boundary moved and the log says nothing"
    assert "terms" in rows[0]["changes"], rows[0]["changes"]


def test_saving_an_unchanged_calendar_records_nothing(client, admin_headers):
    """Opening the page and pressing Save is the common case; logging it would
    bury the real changes."""
    client.put(CALENDAR, headers=admin_headers, json=SCHOOL)
    before = len(_calendar_events(client, admin_headers))

    client.put(CALENDAR, headers=admin_headers, json=SCHOOL)

    assert len(_calendar_events(client, admin_headers)) == before
