import io
import os
import tempfile
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

os.environ["PRINTOPS_COPIER_IMPORT_SPOOL_DIR"] = tempfile.mkdtemp(
    prefix="printops-copier-imports-test-"
)

from app.db import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.google_workspace import GoogleWorkspaceUser  # noqa: E402


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
    async with session_factory() as seed:
        seed.add(
            GoogleWorkspaceUser(
                email="new.hire@district.org",
                name="New Hire",
                employee_id="55555",
                synced_at=datetime.now(UTC),
            )
        )
        await seed.commit()
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


@pytest.fixture
def device_with_unmapped_usage(client, auth_headers):
    device = client.post(
        "/api/v1/mfp-devices",
        headers=auth_headers,
        json={"name": "Front Office MFP", "vendor": "canon", "connector_type": "generic_csv"},
    ).json()
    csv_content = "User Code,Date,Pages\n55555,2026-07-01,10\n55555,2026-07-02,5\n"
    files = {"file": ("export.csv", io.BytesIO(csv_content.encode()), "text/csv")}
    upload = client.post(
        "/api/v1/copier-imports/upload",
        headers=auth_headers,
        data={"device_id": device["id"]},
        files=files,
    ).json()
    client.post(
        f"/api/v1/copier-imports/{upload['batch_id']}/preview",
        headers=auth_headers,
        json={
            "column_mapping": {
                "identity_value": "User Code",
                "occurred_at": "Date",
                "page_count": "Pages",
            },
            "identity_type": "staff_id",
        },
    )
    client.post(
        f"/api/v1/copier-imports/{upload['batch_id']}/commit",
        headers=auth_headers,
        json={"skip_duplicates": True},
    )
    return device


def test_list_unmapped_groups(client, auth_headers, device_with_unmapped_usage):
    response = client.get("/api/v1/copier-unmapped", headers=auth_headers)
    assert response.status_code == 200
    groups = response.json()
    assert len(groups) == 1
    assert groups[0]["external_identity_used"] == "55555"
    assert groups[0]["occurrence_count"] == 2
    assert groups[0]["attempted_identity_type"] == "staff_id"


def test_resolve_backfills_existing_rows_and_creates_identity(
    client, auth_headers, device_with_unmapped_usage
):
    device = device_with_unmapped_usage
    resolve = client.put(
        "/api/v1/copier-unmapped/resolve",
        headers=auth_headers,
        json={
            "mfp_device_id": device["id"],
            "identity_type": "staff_id",
            "identity_value": "55555",
            "resolved_email": "new.hire@district.org",
        },
    )
    assert resolve.status_code == 200, resolve.text
    assert resolve.json()["backfilled_row_count"] == 2

    assert client.get("/api/v1/copier-unmapped", headers=auth_headers).json() == []

    usage = client.get(f"/api/v1/mfp-devices/{device['id']}/usage", headers=auth_headers).json()
    assert all(r["staff_email"] == "new.hire@district.org" for r in usage)
    assert all(r["staff_employee_id"] == "55555" for r in usage)

    identities = client.get(
        "/api/v1/staff-copier-identities/by-staff/new.hire@district.org", headers=auth_headers
    ).json()
    assert len(identities) == 1 and identities[0]["identity_value"] == "55555"


def test_resolve_rejects_email_not_in_roster(client, auth_headers, device_with_unmapped_usage):
    device = device_with_unmapped_usage
    response = client.put(
        "/api/v1/copier-unmapped/resolve",
        headers=auth_headers,
        json={
            "mfp_device_id": device["id"],
            "identity_type": "staff_id",
            "identity_value": "55555",
            "resolved_email": "nobody@district.org",
        },
    )
    assert response.status_code == 400
