import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.base import Base


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


def test_connector_types_only_lists_registered_connectors(client, auth_headers):
    response = client.get("/api/v1/mfp-devices/connector-types", headers=auth_headers)
    assert response.status_code == 200
    assert {c["connector_type"] for c in response.json()} == {
        "generic_csv",
        "generic_snmp",
        "canon_department_id",
        "konica_bizhub",
        "kyocera_department_management",
        "ricoh_user_code_auth",
        "xerox_standard_accounting",
        "lexmark_accounting",
        "hp_access_control",
        "sharp_accounting",
    }


def test_create_rejects_unknown_connector_type(client, auth_headers):
    response = client.post(
        "/api/v1/mfp-devices",
        headers=auth_headers,
        json={"name": "X", "connector_type": "toshiba_accounting"},
    )
    assert response.status_code == 422


def test_create_list_get_delete(client, auth_headers):
    create = client.post(
        "/api/v1/mfp-devices",
        headers=auth_headers,
        json={"name": "Copy Room MFP", "vendor": "canon", "connector_type": "generic_csv"},
    )
    assert create.status_code == 201, create.text
    body = create.json()
    device_id = body["id"]
    # Every capability starts unassessed (None), not a false "unsupported".
    assert all(v is None for v in body["capabilities"].values())
    assert body["capabilities_source"] is None

    listing = client.get("/api/v1/mfp-devices", headers=auth_headers)
    assert listing.status_code == 200 and len(listing.json()) == 1

    get_one = client.get(f"/api/v1/mfp-devices/{device_id}", headers=auth_headers)
    assert get_one.status_code == 200

    deleted = client.delete(f"/api/v1/mfp-devices/{device_id}", headers=auth_headers)
    assert deleted.status_code == 204

    missing = client.get(f"/api/v1/mfp-devices/{device_id}", headers=auth_headers)
    assert missing.status_code == 404


def test_update_capabilities_manually(client, auth_headers):
    device_id = client.post(
        "/api/v1/mfp-devices",
        headers=auth_headers,
        json={"name": "Front Office MFP", "connector_type": "generic_csv"},
    ).json()["id"]

    updated = client.patch(
        f"/api/v1/mfp-devices/{device_id}",
        headers=auth_headers,
        json={"capabilities": {"walkup_copy_accounting": True, "badge_card_auth": False}},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["capabilities"]["walkup_copy_accounting"] is True
    assert body["capabilities"]["badge_card_auth"] is False
    # Untouched capabilities stay unassessed, not silently flipped to False.
    assert body["capabilities"]["user_code_pin_auth"] is None
    assert body["capabilities_source"] == "manual"


def test_test_connection_honest_about_unsupported_connector(client, auth_headers):
    """generic_csv has no live-connection concept — never fakes support."""
    device_id = client.post(
        "/api/v1/mfp-devices",
        headers=auth_headers,
        json={"name": "CSV-only MFP", "connector_type": "generic_csv"},
    ).json()["id"]

    response = client.post(f"/api/v1/mfp-devices/{device_id}/test-connection", headers=auth_headers)
    assert response.status_code == 400
    assert (
        "doesn't support" in response.json()["detail"] or "can't test" in response.json()["detail"]
    )


def test_check_meter_rejects_non_snmp_connector(client, auth_headers):
    device_id = client.post(
        "/api/v1/mfp-devices",
        headers=auth_headers,
        json={"name": "CSV-only MFP", "connector_type": "generic_csv"},
    ).json()["id"]

    response = client.post(f"/api/v1/mfp-devices/{device_id}/check-meter", headers=auth_headers)
    assert response.status_code == 400


def test_admin_password_is_write_only(client, auth_headers):
    """The stored admin password is never echoed back — the API exposes
    only has_admin_password, same masking as snmp_community."""
    create = client.post(
        "/api/v1/mfp-devices",
        headers=auth_headers,
        json={
            "name": "HS Copier - Monica",
            "vendor": "konica_minolta",
            "connector_type": "konica_bizhub",
            "admin_password": "s3cret",
        },
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["has_admin_password"] is True
    assert "admin_password" not in body
    assert "s3cret" not in create.text
    # No username is the correct state for a bizhub, not a missing field.
    assert body["admin_username"] is None

    get_one = client.get(f"/api/v1/mfp-devices/{body['id']}", headers=auth_headers)
    assert "s3cret" not in get_one.text


def test_admin_password_stored_encrypted_not_plaintext(client, auth_headers, db_session_factory):
    """Guards the actual point of the field: what lands in the column is
    ciphertext that decrypt() round-trips, never the plaintext."""
    import asyncio

    from sqlalchemy import select

    from app.core.crypto import decrypt
    from app.models.mfp_device import MfpDevice

    client.post(
        "/api/v1/mfp-devices",
        headers=auth_headers,
        json={
            "name": "ES Veronica Copier",
            "connector_type": "konica_bizhub",
            "admin_password": "s3cret",
        },
    )

    async def read_row():
        async with db_session_factory() as session:
            result = await session.execute(select(MfpDevice))
            return result.scalars().one()

    device = asyncio.get_event_loop().run_until_complete(read_row())
    assert device.admin_password_encrypted != "s3cret"
    assert decrypt(device.admin_password_encrypted) == "s3cret"


def test_admin_password_update_and_clear(client, auth_headers):
    device_id = client.post(
        "/api/v1/mfp-devices",
        headers=auth_headers,
        json={"name": "CO Danica Copier", "connector_type": "konica_bizhub"},
    ).json()["id"]
    assert (
        client.get(f"/api/v1/mfp-devices/{device_id}", headers=auth_headers).json()[
            "has_admin_password"
        ]
        is False
    )

    setpw = client.patch(
        f"/api/v1/mfp-devices/{device_id}",
        headers=auth_headers,
        json={"admin_username": "admin", "admin_password": "hunter2"},
    )
    assert setpw.status_code == 200, setpw.text
    assert setpw.json()["has_admin_password"] is True
    assert setpw.json()["admin_username"] == "admin"

    # An unrelated PATCH must not wipe the credential.
    renamed = client.patch(
        f"/api/v1/mfp-devices/{device_id}",
        headers=auth_headers,
        json={"room": "Main Office"},
    )
    assert renamed.json()["has_admin_password"] is True

    # Empty string clears it, mirroring snmp_community.
    cleared = client.patch(
        f"/api/v1/mfp-devices/{device_id}",
        headers=auth_headers,
        json={"admin_password": ""},
    )
    assert cleared.json()["has_admin_password"] is False


def test_provision_org_unit_paths_round_trip(client, auth_headers):
    """Per-copier OU narrowing: null means "everyone the org-wide filter
    allows", which is a different thing from an empty list."""
    device_id = client.post(
        "/api/v1/mfp-devices",
        headers=auth_headers,
        json={"name": "ES Veronica Copier", "connector_type": "konica_bizhub"},
    ).json()["id"]
    assert (
        client.get(f"/api/v1/mfp-devices/{device_id}", headers=auth_headers).json()[
            "provision_org_unit_paths"
        ]
        is None
    )

    scoped = client.patch(
        f"/api/v1/mfp-devices/{device_id}",
        headers=auth_headers,
        json={"provision_org_unit_paths": ["/Employees/Elementary School"]},
    )
    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["provision_org_unit_paths"] == ["/Employees/Elementary School"]

    cleared = client.patch(
        f"/api/v1/mfp-devices/{device_id}",
        headers=auth_headers,
        json={"provision_org_unit_paths": None},
    )
    assert cleared.json()["provision_org_unit_paths"] is None


# --- Default copy owner -----------------------------------------------------


def _device(client, auth_headers, **overrides):
    payload = {"name": "IT Color Copier", "connector_type": "canon_department_id"}
    payload.update(overrides)
    return client.post("/api/v1/mfp-devices", headers=auth_headers, json=payload).json()


def test_naming_a_default_owner_starts_them_at_zero(client, auth_headers):
    """The watermark stays null until an attribution run stamps it, which
    is what makes the first run a baseline rather than a bill for the
    device's whole lifetime."""
    device_id = _device(client, auth_headers)["id"]

    updated = client.patch(
        f"/api/v1/mfp-devices/{device_id}",
        headers=auth_headers,
        json={"default_owner_email": "manderson@brookfieldr3.org"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["default_owner_email"] == "manderson@brookfieldr3.org"
    assert updated.json()["default_owner_attributed_through"] is None


def test_changing_the_owner_does_not_hand_the_new_one_the_old_one_s_pages(
    client, auth_headers, db_session_factory
):
    import uuid as _uuid
    from datetime import UTC, datetime

    from app.models.mfp_device import MfpDevice

    device_id = _device(client, auth_headers)["id"]
    client.patch(
        f"/api/v1/mfp-devices/{device_id}",
        headers=auth_headers,
        json={"default_owner_email": "first.owner@district.org"},
    )

    async def _stamp():
        async with db_session_factory() as session:
            device = await session.get(MfpDevice, _uuid.UUID(device_id))
            device.default_owner_attributed_through = datetime.now(UTC)
            await session.commit()

    import asyncio

    asyncio.get_event_loop().run_until_complete(_stamp())

    updated = client.patch(
        f"/api/v1/mfp-devices/{device_id}",
        headers=auth_headers,
        json={"default_owner_email": "second.owner@district.org"},
    )
    # Reset — the new owner is credited only from here on, not with every
    # page metered since the previous owner was named.
    assert updated.json()["default_owner_attributed_through"] is None


def test_clearing_the_owner_empties_the_field_rather_than_storing_a_blank(client, auth_headers):
    device_id = _device(client, auth_headers)["id"]
    client.patch(
        f"/api/v1/mfp-devices/{device_id}",
        headers=auth_headers,
        json={"default_owner_email": "manderson@brookfieldr3.org"},
    )

    cleared = client.patch(
        f"/api/v1/mfp-devices/{device_id}",
        headers=auth_headers,
        json={"default_owner_email": ""},
    )
    assert cleared.json()["default_owner_email"] is None


def test_attribute_copies_on_an_unlinked_copier_explains_itself(client, auth_headers):
    device_id = _device(client, auth_headers)["id"]
    client.patch(
        f"/api/v1/mfp-devices/{device_id}",
        headers=auth_headers,
        json={"default_owner_email": "manderson@brookfieldr3.org"},
    )

    response = client.post(
        f"/api/v1/mfp-devices/{device_id}/attribute-copies", headers=auth_headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["usage_rows"] == 0
    assert "isn't linked to a printer" in body["skipped_reason"]
