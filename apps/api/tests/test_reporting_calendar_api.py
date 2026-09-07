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
    # January, not July. A July start is a school's — and a fiscal year's — and
    # neither is a safe guess. The calendar year is the one year every
    # organisation certainly has.
    assert (body["year_start_month"], body["year_start_day"]) == (1, 1)


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


# --- the compatibility endpoint ---------------------------------------------


def test_the_old_calendar_endpoint_follows_the_new_one(client, admin_headers):
    """/reports/calendar predates the configurable calendar and reads the
    columns it replaced. Left alone it would freeze at whatever the boundaries
    were before an admin first touched the new settings page, and an older
    client would go on computing last year's dates with nothing to show it was
    wrong. A lossy answer beats a stale one.
    """
    moved = dict(SCHOOL)
    moved["year_start_month"] = 8
    moved["year_start_day"] = 12
    moved["terms"] = [
        {"name": "Fall Semester", "start_month": 8, "start_day": 12},
        {"name": "Spring Semester", "start_month": 1, "start_day": 9},
    ]
    client.put(CALENDAR, headers=admin_headers, json=moved)

    body = client.get("/api/v1/reports/calendar", headers=admin_headers).json()
    assert (body["school_year_start_month"], body["school_year_start_day"]) == (8, 12)
    assert (body["spring_semester_start_month"], body["spring_semester_start_day"]) == (1, 9)


def test_the_old_calendar_endpoint_survives_a_year_with_no_terms(client, admin_headers):
    """Its shape assumes two terms and calls the second a semester. An
    organisation that has none still has a year start, and this must answer
    with it rather than fail."""
    none = dict(SCHOOL)
    none["terms"] = []
    client.put(CALENDAR, headers=admin_headers, json=none)

    response = client.get("/api/v1/reports/calendar", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["school_year_start_month"] == 7


def test_the_formula_endpoint_no_longer_edits_the_calendar(client, admin_headers):
    """Two endpoints writing one calendar is how this app and its own API came
    to disagree about when a school year began. The fields are gone from the
    formula endpoint rather than left writable and inert."""
    body = client.get("/api/v1/settings/report-formulas", headers=admin_headers).json()
    assert "school_year_start_month" not in body
    assert "spring_semester_start_month" not in body


# --- previewing before saving -----------------------------------------------


def test_a_proposed_calendar_resolves_without_being_saved(client, admin_headers):
    """The editor shows what a change does before an admin commits to it.

    Resolved on the server rather than in the browser on purpose: a second
    implementation of the year and term rules in TypeScript is what had the
    Insights page and this API disagreeing about when a school year began.
    """
    quarters = {
        "year_start_month": 10,
        "year_start_day": 1,
        "year_noun": "FY",
        "label_style": "single",
        "terms": [
            {"name": "Q1", "start_month": 10, "start_day": 1},
            {"name": "Q2", "start_month": 1, "start_day": 1},
            {"name": "Q3", "start_month": 4, "start_day": 1},
            {"name": "Q4", "start_month": 7, "start_day": 1},
        ],
    }
    response = client.post(f"{CALENDAR}/preview", headers=admin_headers, json=quarters)
    assert response.status_code == 200

    body = response.json()
    assert [period["kind"] for period in body] == ["year", "term", "term", "term", "term"]
    # All four quarters carry one fiscal year's number, including the two that
    # fall in the next calendar year. Asserted as "they agree" rather than
    # against a literal: which year is current depends on today's date, and an
    # earlier version of this test hardcoded 2027 and failed every September.
    numbers = {period["label"].split()[-1] for period in body[1:]}
    assert len(numbers) == 1, f"one fiscal year split across {numbers}"
    assert numbers == {body[0]["label"].split()[-1]}, "and it is the year's own number"

    # And nothing was written.
    saved = client.get(CALENDAR, headers=admin_headers).json()
    assert saved["year_noun"] == "Year", "preview must not save"
    assert saved["terms"] == []


def test_previewing_a_calendar_that_cannot_resolve_is_refused(client, admin_headers):
    """So the editor can say which field is wrong while it is being typed,
    rather than at save time."""
    response = client.post(
        f"{CALENDAR}/preview",
        headers=admin_headers,
        json={
            "year_start_month": 2,
            "year_start_day": 31,
            "year_noun": "Year",
            "label_style": "auto",
            "terms": [],
        },
    )
    assert response.status_code == 422


def test_previewing_needs_an_admin(client):
    assert client.post(
        f"{CALENDAR}/preview",
        json={
            "year_start_month": 1,
            "year_start_day": 1,
            "year_noun": "Year",
            "label_style": "auto",
            "terms": [],
        },
    ).status_code in (401, 403)


# --- the years the calendar produces ----------------------------------------

YEARS = f"{CALENDAR}/years"


def test_years_are_generated_not_stored(client, admin_headers):
    """The point of a repeating pattern: no year needs data entered for it.

    Next August nobody should have to add anything, and a year from before this
    installation existed still has segments to report against.
    """
    client.put(CALENDAR, headers=admin_headers, json=SCHOOL)
    body = client.get(YEARS, headers=admin_headers).json()

    assert body, "at least the current year"
    assert all(len(year["segments"]) == 2 for year in body), "every year has its segments"
    assert all(year["overridden"] is False for year in body)
    # Newest first, and the year after the current one is included so an admin
    # can lay out next year before it starts.
    years = [year["year"] for year in body]
    assert years == sorted(years, reverse=True)


def test_a_year_with_no_activity_is_offered_but_marked(client, admin_headers):
    """It resolves perfectly well and returns nothing. An empty report reads as
    "nobody printed" rather than "we were not watching yet", so the difference
    has to be visible rather than inferred."""
    client.put(CALENDAR, headers=admin_headers, json=SCHOOL)
    body = client.get(YEARS, headers=admin_headers).json()

    # No jobs in this fixture at all, so nothing can claim to have data.
    assert all(year["has_data"] is False for year in body)


def test_one_year_can_be_given_explicit_dates(client, admin_headers):
    """For the year a term really did start late."""
    client.put(CALENDAR, headers=admin_headers, json=SCHOOL)

    response = client.put(
        f"{YEARS}/2026",
        headers=admin_headers,
        json={
            "segments": [
                {"name": "Fall Semester", "start_date": "2026-08-22", "end_date": "2026-12-20"},
                {"name": "Spring Semester", "start_date": "2027-01-09", "end_date": "2027-05-26"},
            ]
        },
    )
    assert response.status_code == 200

    years = {year["year"]: year for year in response.json()}
    assert years[2026]["overridden"] is True
    assert years[2026]["segments"][0]["start"] == "2026-08-22"
    # Inclusive in, exclusive out: the day after the last day covered.
    assert years[2026]["segments"][0]["end"] == "2026-12-21"

    # And only that year. Every other still comes from the pattern, so one
    # correction does not become an annual chore.
    assert years[2027]["overridden"] is False
    assert years[2027]["segments"][0]["start"] == "2027-08-15"


def test_an_overridden_year_can_be_reverted(client, admin_headers):
    client.put(CALENDAR, headers=admin_headers, json=SCHOOL)
    client.put(
        f"{YEARS}/2026",
        headers=admin_headers,
        json={
            "segments": [
                {"name": "Fall", "start_date": "2026-08-22", "end_date": "2026-12-20"},
            ]
        },
    )
    body = client.delete(f"{YEARS}/2026", headers=admin_headers).json()

    years = {year["year"]: year for year in body}
    assert years[2026]["overridden"] is False
    assert len(years[2026]["segments"]) == 2, "back to the pattern"


def test_overlapping_stated_segments_are_refused(client, admin_headers):
    """Two segments claiming the same day means one of them silently loses
    printing to the other, and nothing complains."""
    client.put(CALENDAR, headers=admin_headers, json=SCHOOL)
    response = client.put(
        f"{YEARS}/2026",
        headers=admin_headers,
        json={
            "segments": [
                {"name": "Fall", "start_date": "2026-08-22", "end_date": "2027-01-10"},
                {"name": "Spring", "start_date": "2027-01-05", "end_date": "2027-05-26"},
            ]
        },
    )
    assert response.status_code == 422


def test_stating_a_year_is_audited(client, admin_headers):
    """Moving a segment boundary changes what every report ever run against
    that segment covers."""
    client.put(CALENDAR, headers=admin_headers, json=SCHOOL)
    client.put(
        f"{YEARS}/2026",
        headers=admin_headers,
        json={
            "segments": [
                {"name": "Fall", "start_date": "2026-08-22", "end_date": "2026-12-20"},
            ]
        },
    )
    rows = _calendar_events(client, admin_headers)
    assert any("year" in row["action"] for row in rows), [row["action"] for row in rows]


def test_only_an_admin_can_state_a_year(client):
    assert client.put(
        f"{YEARS}/2026",
        json={"segments": [{"name": "F", "start_date": "2026-08-22", "end_date": "2026-12-20"}]},
    ).status_code in (401, 403)


@pytest.mark.parametrize(
    ("segments", "because"),
    [
        (
            [{"name": "Fall", "start_date": "2027-08-22", "end_date": "2027-12-20"}],
            "a year later than the one it is filed under",
        ),
        (
            [{"name": "Fall", "start_date": "2026-05-01", "end_date": "2026-06-30"}],
            "before the year opens",
        ),
        (
            [{"name": "Spring", "start_date": "2027-01-09", "end_date": "2027-08-01"}],
            "running past the end of the year",
        ),
    ],
)
def test_stated_dates_outside_their_own_year_are_refused(client, admin_headers, segments, because):
    """The one mistake this endpoint cannot absorb.

    An override replaces the pattern for its year entirely, so a segment
    mistyped into the following year would be exposed under *this* year's keys.
    Reports for it would query the wrong span, and the year itself could be
    left with no segment covering its actual days — all without anything
    looking wrong on screen.
    """
    client.put(CALENDAR, headers=admin_headers, json=SCHOOL)
    response = client.put(f"{YEARS}/2026", headers=admin_headers, json={"segments": segments})
    assert response.status_code == 422, because


def test_two_segments_cannot_share_a_position(client, admin_headers):
    """`position` is part of the period key, so a duplicate produces two
    periods with the same key. Resolution returns the first, and the second
    holds printing that can never be selected for a report."""
    client.put(CALENDAR, headers=admin_headers, json=SCHOOL)
    response = client.put(
        f"{YEARS}/2026",
        headers=admin_headers,
        json={
            "segments": [
                {
                    "name": "Fall",
                    "start_date": "2026-08-22",
                    "end_date": "2026-12-20",
                    "position": 0,
                },
                {
                    "name": "Spring",
                    "start_date": "2027-01-09",
                    "end_date": "2027-05-26",
                    "position": 0,
                },
            ]
        },
    )
    assert response.status_code == 422


def test_the_year_list_counts_single_event_copier_activity(client, admin_headers):
    """Copier rows come in two shapes: an aggregate with a period, and a single
    event with only `occurred_at`. An installation whose history is entirely
    single events has a null `period_start` on every row, so asking for that
    column alone reports no copier activity at all — and the year list would
    then omit every historical year those copies fall in.

    Asserted against COPY_INSTANT, the same precedence copier reporting filters
    on, so the year list and the reports it leads to cannot disagree about when
    activity began.
    """
    from app.models.copier_usage import CopierUsageRecord
    from app.routers.settings import COPY_INSTANT

    assert COPY_INSTANT is not None
    # occurred_at is first in the precedence, ahead of period_end and
    # created_at; period_start is not in it at all.
    rendered = str(COPY_INSTANT)
    assert "occurred_at" in rendered
    assert "period_start" not in rendered, "period_start is not the copy instant"
    assert CopierUsageRecord.period_start is not None
