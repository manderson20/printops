import io
import os
import tempfile
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

os.environ["PRINTOPS_COPIER_IMPORT_SPOOL_DIR"] = tempfile.mkdtemp(prefix="printops-copier-imports-test-")

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
                email="jane.smith@district.org",
                name="Jane Smith",
                employee_id="12345",
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
def device(client, auth_headers):
    return client.post(
        "/api/v1/mfp-devices",
        headers=auth_headers,
        json={"name": "Copy Room MFP", "vendor": "canon", "connector_type": "generic_csv"},
    ).json()


def _upload(client, auth_headers, device_id, csv_content, filename="export.csv"):
    files = {"file": (filename, io.BytesIO(csv_content.encode()), "text/csv")}
    return client.post(
        "/api/v1/copier-imports/upload",
        headers=auth_headers,
        data={"device_id": device_id},
        files=files,
    )


DEFAULT_MAPPING = {"identity_value": "User Code", "occurred_at": "Date", "page_count": "Pages"}


def test_upload_detects_header_and_row_count(client, auth_headers, device):
    csv_content = "User Code,Date,Pages\n12345,2026-07-01,42\n"
    upload = _upload(client, auth_headers, device["id"], csv_content)
    assert upload.status_code == 201, upload.text
    body = upload.json()
    assert body["header"] == ["User Code", "Date", "Pages"]
    assert body["row_count"] == 1


def test_preview_flags_unmapped_and_no_duplicates_before_commit(client, auth_headers, device):
    client.post(
        "/api/v1/staff-copier-identities",
        headers=auth_headers,
        json={"staff_email": "jane.smith@district.org", "identity_type": "staff_id", "identity_value": "12345"},
    )
    csv_content = "User Code,Date,Pages\n12345,2026-07-01,42\n99999,2026-07-01,7\n"
    batch_id = _upload(client, auth_headers, device["id"], csv_content).json()["batch_id"]

    preview = client.post(
        f"/api/v1/copier-imports/{batch_id}/preview",
        headers=auth_headers,
        json={"column_mapping": DEFAULT_MAPPING, "identity_type": "staff_id"},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["total_rows"] == 2
    assert body["unmapped_rows"] == 1
    assert body["duplicate_rows"] == 0


def test_commit_persists_rows_and_resolves_known_identities(client, auth_headers, device):
    client.post(
        "/api/v1/staff-copier-identities",
        headers=auth_headers,
        json={"staff_email": "jane.smith@district.org", "identity_type": "staff_id", "identity_value": "12345"},
    )
    csv_content = "User Code,Date,Pages\n12345,2026-07-01,42\n99999,2026-07-01,7\n"
    batch_id = _upload(client, auth_headers, device["id"], csv_content).json()["batch_id"]
    client.post(
        f"/api/v1/copier-imports/{batch_id}/preview",
        headers=auth_headers,
        json={"column_mapping": DEFAULT_MAPPING, "identity_type": "staff_id"},
    )
    commit = client.post(
        f"/api/v1/copier-imports/{batch_id}/commit", headers=auth_headers, json={"skip_duplicates": True}
    )
    assert commit.status_code == 200, commit.text
    assert commit.json()["status"] == "committed"
    assert commit.json()["imported_row_count"] == 2

    usage = client.get(f"/api/v1/mfp-devices/{device['id']}/usage", headers=auth_headers).json()
    assert len(usage) == 2
    mapped = [r for r in usage if r["staff_email"] == "jane.smith@district.org"]
    assert len(mapped) == 1 and mapped[0]["page_count"] == 42


def test_recommitting_same_file_is_detected_as_duplicate(client, auth_headers, device):
    csv_content = "User Code,Date,Pages\n12345,2026-07-01,42\n"
    batch_id = _upload(client, auth_headers, device["id"], csv_content).json()["batch_id"]
    client.post(
        f"/api/v1/copier-imports/{batch_id}/preview",
        headers=auth_headers,
        json={"column_mapping": DEFAULT_MAPPING, "identity_type": "staff_id"},
    )
    client.post(
        f"/api/v1/copier-imports/{batch_id}/commit", headers=auth_headers, json={"skip_duplicates": True}
    )

    batch_id_2 = _upload(client, auth_headers, device["id"], csv_content).json()["batch_id"]
    preview2 = client.post(
        f"/api/v1/copier-imports/{batch_id_2}/preview",
        headers=auth_headers,
        json={"column_mapping": DEFAULT_MAPPING, "identity_type": "staff_id"},
    ).json()
    assert preview2["duplicate_rows"] == 1

    commit2 = client.post(
        f"/api/v1/copier-imports/{batch_id_2}/commit",
        headers=auth_headers,
        json={"skip_duplicates": True},
    ).json()
    assert commit2["imported_row_count"] == 0


def test_committed_batch_cannot_be_deleted(client, auth_headers, device):
    csv_content = "User Code,Date,Pages\n12345,2026-07-01,42\n"
    batch_id = _upload(client, auth_headers, device["id"], csv_content).json()["batch_id"]
    client.post(
        f"/api/v1/copier-imports/{batch_id}/preview",
        headers=auth_headers,
        json={"column_mapping": DEFAULT_MAPPING, "identity_type": "staff_id"},
    )
    client.post(
        f"/api/v1/copier-imports/{batch_id}/commit", headers=auth_headers, json={"skip_duplicates": True}
    )
    deleted = client.delete(f"/api/v1/copier-imports/batches/{batch_id}", headers=auth_headers)
    assert deleted.status_code == 400


def test_uncommitted_batch_can_be_deleted(client, auth_headers, device):
    csv_content = "User Code,Date,Pages\n12345,2026-07-01,42\n"
    batch_id = _upload(client, auth_headers, device["id"], csv_content).json()["batch_id"]
    deleted = client.delete(f"/api/v1/copier-imports/batches/{batch_id}", headers=auth_headers)
    assert deleted.status_code == 204
