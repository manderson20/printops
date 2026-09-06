"""The audit trail records what admins change, and cannot disagree with reality.

The design rests on one property: the audit row is written into the endpoint's
own session, so it commits with the change or not at all. That is what makes it
an audit log rather than a best-effort trace, and it is pinned here directly
rather than assumed — a log that can record a change that did not happen, or
miss one that did, is worse than no log, because it will be believed.

The second thing pinned here is that secrets never reach the table. Settings
carry SNMP community strings, OAuth client secrets and bind passwords, and an
audit row saying "community changed from public to s3cret" would turn the
compliance feature into a credential dump readable by every admin.
"""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.audit.record import REDACTED, diff, record_audit, snapshot
from app.db import get_db
from app.main import app
from app.models.audit import AuditEvent
from app.models.base import Base
from app.models.snmp import SnmpDefaultsSettings
from app.schemas.auth import UserOut


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
def auth_headers(client):
    response = client.post("/auth/login", json={"username": "admin", "password": "changeme"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


ADMIN = UserOut(username="admin", role="admin", subject="admin")


async def _events(session_factory) -> list[AuditEvent]:
    async with session_factory() as db:
        result = await db.execute(select(AuditEvent).order_by(AuditEvent.occurred_at))
        return list(result.scalars().all())


# --- the property the whole design rests on --------------------------------


@pytest.mark.asyncio
async def test_the_event_and_the_change_commit_together(db_session_factory):
    async with db_session_factory() as db:
        settings = SnmpDefaultsSettings()
        db.add(settings)
        await db.commit()

    async with db_session_factory() as db:
        settings = (await db.execute(select(SnmpDefaultsSettings))).scalar_one()
        settings.port = 1161
        record_audit(db, ADMIN, action="settings.snmp.update", summary="Changed the port")
        await db.commit()

    assert len(await _events(db_session_factory)) == 1
    async with db_session_factory() as db:
        assert (await db.execute(select(SnmpDefaultsSettings))).scalar_one().port == 1161


@pytest.mark.asyncio
async def test_a_rolled_back_change_leaves_no_event(db_session_factory):
    """The half that matters. If the change does not land, the claim that it
    landed must not either — and because they share a transaction, that is not
    something the endpoint has to remember to undo."""
    async with db_session_factory() as db:
        settings = SnmpDefaultsSettings(port=161)
        db.add(settings)
        await db.commit()

    async with db_session_factory() as db:
        settings = (await db.execute(select(SnmpDefaultsSettings))).scalar_one()
        settings.port = 9999
        record_audit(db, ADMIN, action="settings.snmp.update", summary="Changed the port")
        await db.rollback()

    assert await _events(db_session_factory) == []
    async with db_session_factory() as db:
        assert (await db.execute(select(SnmpDefaultsSettings))).scalar_one().port == 161


# --- secrets ----------------------------------------------------------------


def test_a_secret_is_recorded_as_changed_without_its_value():
    before = {"community_encrypted": "old-ciphertext", "port": 161}
    after = {"community_encrypted": "new-ciphertext", "port": 161}

    changes = diff(before, after, ["community_encrypted", "port"])

    assert changes == {"community_encrypted": {"from": REDACTED, "to": REDACTED}}
    assert "old-ciphertext" not in str(changes)
    assert "new-ciphertext" not in str(changes)


@pytest.mark.parametrize(
    "field",
    [
        "admin_password_encrypted",
        "client_secret_encrypted",
        "snmp_community_encrypted",
        "access_token_encrypted",
        "ldap_bind_password_hash",
        "api_token",
        "release_token",
    ],
)
def test_every_secret_column_in_this_codebase_is_redacted(field):
    """Named individually rather than generated, so that a column being renamed
    out of the list is a visible deletion in a diff rather than a quiet gap."""
    changes = diff({field: "before"}, {field: "after"}, [field])
    assert changes[field] == {"from": REDACTED, "to": REDACTED}


def test_a_hand_built_changes_dict_is_redacted_too():
    """diff() is the normal path, but record_audit accepts a dict directly and
    is the last point before the value becomes durable."""
    event = record_audit(
        _NullSession(),
        ADMIN,
        action="settings.zabbix.update",
        summary="Rotated the token",
        changes={"api_token": {"from": "plaintext-a", "to": "plaintext-b"}},
    )
    assert event.changes == {"api_token": {"from": REDACTED, "to": REDACTED}}
    assert "plaintext" not in str(event.changes)


class _NullSession:
    """record_audit only calls db.add(); nothing here needs a real session."""

    def add(self, _obj):
        return None


# --- diffing ----------------------------------------------------------------


def test_an_unchanged_field_is_not_recorded():
    """A settings page saved without edits should produce no row at all, not a
    row saying nothing happened — otherwise the log fills with them and the real
    changes get harder to find."""
    assert diff({"port": 161}, {"port": 161}, ["port"]) == {}


def test_a_field_the_caller_did_not_name_is_not_recorded():
    changes = diff({"a": 1, "b": 1}, {"a": 2, "b": 2}, ["a"])
    assert changes == {"a": {"from": 1, "to": 2}}


def test_values_that_cannot_be_json_are_stringified():
    """`changes` is a JSON column and the flush happens inside the caller's
    transaction, so an unserialisable value would turn an audit bug into a
    failed admin action. Better an ugly diff than a rolled-back save."""
    from uuid import UUID

    value = UUID("11111111-2222-3333-4444-555555555555")
    changes = diff({"x": None}, {"x": value}, ["x"])
    assert changes["x"]["to"] == str(value)


def test_snapshot_taken_after_a_mutation_sees_the_new_value(db_session_factory):
    """Documents the trap rather than guarding against it: SQLAlchemy hands out
    live objects, so a `before` snapshot taken after the mutation compares the
    new value with itself and the row says nothing changed."""
    settings = SnmpDefaultsSettings(port=161)
    before = snapshot(settings, ["port"])
    settings.port = 1161
    too_late = snapshot(settings, ["port"])

    assert diff(before, snapshot(settings, ["port"]), ["port"]) != {}
    assert diff(too_late, snapshot(settings, ["port"]), ["port"]) == {}


# --- through the API --------------------------------------------------------


def _settings_events(client, auth_headers, prefix="settings"):
    """Filtered to the change under test. The auth_headers fixture signs in,
    and signing in is itself an audited action now — so an unfiltered list
    always carries an auth.login row that has nothing to do with the assertion."""
    return client.get(
        "/api/v1/audit", params={"action_prefix": prefix}, headers=auth_headers
    ).json()["events"]


def test_changing_a_setting_writes_a_readable_row(client, auth_headers, db_session_factory):
    response = client.put("/api/v1/settings/snmp", json={"port": 1161}, headers=auth_headers)
    assert response.status_code == 200

    events = _settings_events(client, auth_headers, "settings.snmp")
    assert len(events) == 1
    event = events[0]
    assert event["action"] == "settings.snmp.update"
    assert event["actor_email"] == "admin"
    assert event["summary"] == "Updated SNMP settings"
    assert event["changes"]["port"] == {"from": 161, "to": 1161}


def test_saving_a_setting_unchanged_writes_nothing(client, auth_headers):
    current = client.get("/api/v1/settings/snmp", headers=auth_headers).json()
    client.put("/api/v1/settings/snmp", json={"port": current["port"]}, headers=auth_headers)

    assert _settings_events(client, auth_headers, "settings.snmp") == []


def test_setting_an_snmp_community_never_stores_it(client, auth_headers):
    client.put(
        "/api/v1/settings/snmp",
        json={"community": "not-public-at-all"},
        headers=auth_headers,
    )

    body = client.get("/api/v1/audit", headers=auth_headers).text
    assert "not-public-at-all" not in body
    events = _settings_events(client, auth_headers, "settings.snmp")
    assert events[0]["changes"]["community_encrypted"] == {"from": REDACTED, "to": REDACTED}


def test_the_log_is_admin_only(client, auth_headers, db_session_factory):
    """It records what admins did to the system, including to each other's
    accounts. A viewer reading it would be handed an activity feed of the people
    above them."""
    from app.deps import get_current_user

    app.dependency_overrides[get_current_user] = lambda: UserOut(
        username="viewer@example.org", role="viewer", subject="viewer@example.org"
    )
    try:
        assert client.get("/api/v1/audit").status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_retention_cannot_be_set_below_the_floor(client, auth_headers):
    """The obvious way to bury an action is to shorten retention past it."""
    assert (
        client.put(
            "/api/v1/audit/settings", json={"retention_days": 1}, headers=auth_headers
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/v1/audit/settings", json={"retention_days": 400}, headers=auth_headers
        ).status_code
        == 200
    )


def test_changing_retention_is_itself_recorded(client, auth_headers):
    client.put("/api/v1/audit/settings", json={"retention_days": 365}, headers=auth_headers)

    events = _settings_events(client, auth_headers, "settings.audit")
    assert [e["action"] for e in events] == ["settings.audit.update"]
    assert events[0]["changes"]["retention_days"] == {"from": 400, "to": 365}


def test_actions_are_filterable_by_prefix(client, auth_headers):
    client.put("/api/v1/settings/snmp", json={"port": 1161}, headers=auth_headers)
    client.put("/api/v1/audit/settings", json={"retention_days": 365}, headers=auth_headers)

    everything = client.get(
        "/api/v1/audit", params={"action_prefix": "settings"}, headers=auth_headers
    ).json()
    snmp_only = client.get(
        "/api/v1/audit", params={"action_prefix": "settings.snmp"}, headers=auth_headers
    ).json()

    assert everything["total"] == 2
    assert snmp_only["total"] == 1
    assert snmp_only["events"][0]["action"] == "settings.snmp.update"


def test_signing_in_is_recorded(client, auth_headers):
    """auth_headers already signed in — this asserts the row that fact leaves."""
    events = client.get(
        "/api/v1/audit", params={"action_prefix": "auth"}, headers=auth_headers
    ).json()["events"]

    assert [e["action"] for e in events] == ["auth.login"]
    assert events[0]["actor_email"] == "admin"


def test_a_failed_sign_in_never_records_the_attempted_username(client, auth_headers):
    """People type their password into the username box. An audit trail that
    captured those would hand every admin a list of near-miss credentials —
    turning the compliance feature into the thing it protects against."""
    client.post("/auth/login", json={"username": "hunter2-my-actual-password", "password": "x"})

    response = client.get("/api/v1/audit", params={"action_prefix": "auth"}, headers=auth_headers)
    assert "hunter2-my-actual-password" not in response.text

    failures = [e for e in response.json()["events"] if e["action"] == "auth.login.failed"]
    assert len(failures) == 1
    assert failures[0]["actor_email"] == "(unrecognised username)"


def test_a_failed_sign_in_to_the_real_account_names_it(client, auth_headers):
    """The useful signal survives the redaction: somebody hammering the admin
    account reads differently from somebody spraying names that do not exist."""
    client.post("/auth/login", json={"username": "admin", "password": "wrong"})

    events = client.get(
        "/api/v1/audit", params={"action_prefix": "auth.login.failed"}, headers=auth_headers
    ).json()["events"]
    assert [e["actor_email"] for e in events] == ["admin"]
