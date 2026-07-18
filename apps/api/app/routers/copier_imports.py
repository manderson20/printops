"""CSV accounting import — the fallback every copier vendor gets, and the
only Stage 1 way to bring in per-user copy accounting for anything without
a real API connector yet (which, in Stage 1, is every vendor).

Upload -> map columns (optionally saving a reusable template) -> preview
(dry-run parse against the actual staff-copier-identity roster, no writes)
-> commit (persist CopierUsageRecord rows). Preview and commit share one
parser (_parse_batch) so what an admin sees in preview is exactly what
commit will do — never an approximation.
"""

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.copiers.connector import NormalizedUsageRow
from app.copiers.generic_csv import CsvParseError, normalize_csv_row, parse_csv_rows
from app.db import get_db
from app.deps import get_current_user, require_role
from app.models.copier_import import CopierImportBatch, CopierImportTemplate
from app.models.copier_usage import CopierUsageRecord
from app.models.google_workspace import GoogleWorkspaceUser
from app.models.mfp_device import MfpDevice
from app.models.staff_copier_identity import StaffCopierIdentity
from app.schemas.auth import UserOut
from app.schemas.copier_import import (
    CopierImportBatchOut,
    CopierImportCommitRequest,
    CopierImportPreviewOut,
    CopierImportPreviewRequest,
    CopierImportTemplateCreate,
    CopierImportTemplateOut,
    CopierImportTemplateUpdate,
    CopierImportUploadOut,
    PreviewRowOut,
)

router = APIRouter(dependencies=[Depends(require_role("admin"))])


# Unlike /var/spool/printops-held (root:lp, group-writable, created by the
# root-owned CUPS backend script — see infra/cups/backends/printops), this
# directory is only ever touched by this API process, so it lives under
# the repo tree rather than /var/spool — the API service's own user can't
# create directories under /var/spool itself (root-owned 755).
_REPO_ROOT = Path(__file__).resolve().parents[4]
SPOOL_DIR = Path(
    os.environ.get("PRINTOPS_COPIER_IMPORT_SPOOL_DIR", str(_REPO_ROOT / "var" / "copier-imports"))
)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SAMPLE_ROW_COUNT = 20


# ---- Templates ----


@router.get("/templates", response_model=list[CopierImportTemplateOut])
async def list_import_templates(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(CopierImportTemplate).order_by(CopierImportTemplate.name))
    return result.scalars().all()


@router.post(
    "/templates", response_model=CopierImportTemplateOut, status_code=status.HTTP_201_CREATED
)
async def create_import_template(
    payload: CopierImportTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
):
    template = CopierImportTemplate(**payload.model_dump(), created_by=current_user.username)
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template


@router.patch("/templates/{template_id}", response_model=CopierImportTemplateOut)
async def update_import_template(
    template_id: UUID, payload: CopierImportTemplateUpdate, db: AsyncSession = Depends(get_db)
):
    template = await db.get(CopierImportTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(template, field, value)
    await db.commit()
    await db.refresh(template)
    return template


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_import_template(template_id: UUID, db: AsyncSession = Depends(get_db)):
    template = await db.get(CopierImportTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    await db.delete(template)
    await db.commit()


# ---- Upload ----


async def _get_device_or_404(device_id: UUID, db: AsyncSession) -> MfpDevice:
    device = await db.get(MfpDevice, device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MFP device not found")
    return device


async def _get_batch_or_404(batch_id: UUID, db: AsyncSession) -> CopierImportBatch:
    batch = await db.get(CopierImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import batch not found")
    return batch


@router.post("/upload", response_model=CopierImportUploadOut, status_code=status.HTTP_201_CREATED)
async def upload_import_file(
    device_id: UUID = Form(...),
    template_id: UUID | None = Form(None),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
):
    device = await _get_device_or_404(device_id, db)

    template: CopierImportTemplate | None = None
    if template_id is not None:
        template = await db.get(CopierImportTemplate, template_id)
        if template is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    raw_bytes = await file.read()
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB import limit.",
        )

    delimiter = template.delimiter if template else ","
    header, raw_rows = parse_csv_rows(raw_bytes, delimiter)
    if not header:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not detect any columns in this file.",
        )

    if template is not None:
        missing = [col for col in template.column_mapping.values() if col not in header]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Template expects columns not found in this file: {', '.join(missing)}",
            )

    batch_id = uuid4()
    SPOOL_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SPOOL_DIR, 0o700)
    raw_file_path = SPOOL_DIR / f"{batch_id}.csv"
    raw_file_path.write_bytes(raw_bytes)
    os.chmod(raw_file_path, 0o600)

    batch = CopierImportBatch(
        id=batch_id,
        mfp_device_id=device.id,
        template_id=template.id if template else None,
        original_filename=file.filename or "upload.csv",
        raw_file_path=str(raw_file_path),
        uploaded_by=current_user.username,
        status="uploaded",
        row_count=len(raw_rows),
    )
    db.add(batch)
    await db.commit()

    return CopierImportUploadOut(
        batch_id=batch.id,
        original_filename=batch.original_filename,
        header=header,
        sample_rows=raw_rows[:SAMPLE_ROW_COUNT],
        suggested_mapping=template.column_mapping if template else None,
        suggested_identity_type=template.identity_type if template else None,
        row_count=len(raw_rows),
    )


# ---- Shared parse logic (preview + commit) ----


@dataclass
class ParsedRow:
    row_number: int
    normalized: NormalizedUsageRow | None
    staff_email: str | None
    staff_employee_id: str | None
    is_duplicate: bool
    error: str | None


@dataclass
class ParsedBatch:
    header: list[str]
    rows: list[ParsedRow]
    total_rows: int
    valid_rows: int
    duplicate_rows: int
    unmapped_rows: int
    error_rows: int


async def _resolve_identity(
    db: AsyncSession, mfp_device_id: UUID, identity_type: str, identity_value: str
) -> tuple[str | None, str | None]:
    """Device-scoped identity wins over an org-wide one with the same
    (identity_type, identity_value) — e.g. a Department ID configured
    locally on one copier takes priority over a coincidentally-identical
    org-wide code."""
    result = await db.execute(
        select(StaffCopierIdentity)
        .where(
            StaffCopierIdentity.identity_type == identity_type,
            StaffCopierIdentity.identity_value == identity_value,
            # NULL never satisfies IN (...) in SQL, so the org-wide case
            # (mfp_device_id IS NULL) needs its own explicit OR branch.
            or_(
                StaffCopierIdentity.mfp_device_id == mfp_device_id,
                StaffCopierIdentity.mfp_device_id.is_(None),
            ),
        )
        .order_by(StaffCopierIdentity.mfp_device_id.isnot(None).desc())
    )
    identity = result.scalars().first()
    if identity is None:
        return None, None

    employee_id_result = await db.execute(
        select(GoogleWorkspaceUser.employee_id).where(
            func.lower(GoogleWorkspaceUser.email) == identity.staff_email.lower()
        )
    )
    return identity.staff_email, employee_id_result.scalar_one_or_none()


async def _is_duplicate(db: AsyncSession, mfp_device_id: UUID, row: NormalizedUsageRow) -> bool:
    stmt = select(CopierUsageRecord.id).where(
        CopierUsageRecord.mfp_device_id == mfp_device_id,
        CopierUsageRecord.external_identity_used == row.external_identity_used,
    )
    if row.occurred_at is not None:
        stmt = stmt.where(CopierUsageRecord.occurred_at == row.occurred_at)
    else:
        stmt = stmt.where(
            CopierUsageRecord.period_start == row.period_start,
            CopierUsageRecord.period_end == row.period_end,
        )
    return (await db.execute(stmt.limit(1))).scalar_one_or_none() is not None


async def _parse_batch(
    db: AsyncSession, batch: CopierImportBatch, mapping: dict[str, str], identity_type: str
) -> ParsedBatch:
    delimiter = ","
    if batch.template_id is not None:
        template = await db.get(CopierImportTemplate, batch.template_id)
        if template is not None:
            delimiter = template.delimiter

    raw_bytes = Path(batch.raw_file_path).read_bytes()
    header, raw_rows = parse_csv_rows(raw_bytes, delimiter)

    rows: list[ParsedRow] = []
    valid = duplicate = unmapped = errored = 0
    for row_number, raw_row in enumerate(raw_rows, start=1):
        try:
            normalized = normalize_csv_row(raw_row, mapping, row_number)
        except CsvParseError as exc:
            rows.append(ParsedRow(row_number, None, None, None, is_duplicate=False, error=str(exc)))
            errored += 1
            continue

        staff_email, staff_employee_id = await _resolve_identity(
            db, batch.mfp_device_id, identity_type, normalized.external_identity_used
        )
        is_dup = await _is_duplicate(db, batch.mfp_device_id, normalized)
        rows.append(ParsedRow(row_number, normalized, staff_email, staff_employee_id, is_dup, None))
        if is_dup:
            duplicate += 1
        else:
            valid += 1
        if staff_email is None:
            unmapped += 1

    return ParsedBatch(
        header=header,
        rows=rows,
        total_rows=len(raw_rows),
        valid_rows=valid,
        duplicate_rows=duplicate,
        unmapped_rows=unmapped,
        error_rows=errored,
    )


# ---- Preview ----


@router.post("/{batch_id}/preview", response_model=CopierImportPreviewOut)
async def preview_import_batch(
    batch_id: UUID, payload: CopierImportPreviewRequest, db: AsyncSession = Depends(get_db)
):
    batch = await _get_batch_or_404(batch_id, db)
    if batch.status == "committed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="This batch has already been committed."
        )

    parsed = await _parse_batch(db, batch, payload.column_mapping, payload.identity_type)

    batch.column_mapping = payload.column_mapping
    batch.identity_type = payload.identity_type
    batch.period_label = payload.period_label
    batch.status = "previewed"
    batch.row_count = parsed.total_rows
    batch.duplicate_row_count = parsed.duplicate_rows
    batch.unmapped_identity_count = parsed.unmapped_rows
    batch.error_detail = [
        {"row_number": r.row_number, "message": r.error} for r in parsed.rows if r.error
    ] or None

    saved_template_id = None
    if payload.save_as_template is not None:
        template = CopierImportTemplate(**payload.save_as_template.model_dump())
        db.add(template)
        await db.flush()
        saved_template_id = template.id
        batch.template_id = template.id

    await db.commit()

    return CopierImportPreviewOut(
        batch_id=batch.id,
        total_rows=parsed.total_rows,
        valid_rows=parsed.valid_rows,
        duplicate_rows=parsed.duplicate_rows,
        unmapped_rows=parsed.unmapped_rows,
        error_rows=parsed.error_rows,
        sample_rows=[
            PreviewRowOut(
                row_number=r.row_number,
                external_identity_used=r.normalized.external_identity_used
                if r.normalized
                else None,
                staff_email=r.staff_email,
                is_duplicate=r.is_duplicate,
                error=r.error,
            )
            for r in parsed.rows[:SAMPLE_ROW_COUNT]
        ],
        saved_template_id=saved_template_id,
    )


# ---- Commit ----


@router.post("/{batch_id}/commit", response_model=CopierImportBatchOut)
async def commit_import_batch(
    batch_id: UUID,
    payload: CopierImportCommitRequest,
    db: AsyncSession = Depends(get_db),
):
    batch = await _get_batch_or_404(batch_id, db)
    if batch.status == "committed":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Already committed.")
    if batch.column_mapping is None or batch.identity_type is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Run preview before committing — no column mapping saved on this batch yet.",
        )

    device = await _get_device_or_404(batch.mfp_device_id, db)
    # Never trust client-echoed preview numbers — re-parse fresh, since the
    # underlying StaffCopierIdentity roster may have changed since preview.
    parsed = await _parse_batch(db, batch, batch.column_mapping, batch.identity_type)

    imported_count = 0
    for row in parsed.rows:
        if row.error or row.normalized is None:
            continue
        if row.is_duplicate and payload.skip_duplicates:
            continue
        normalized = row.normalized
        db.add(
            CopierUsageRecord(
                mfp_device_id=device.id,
                vendor=device.vendor,
                model=device.model,
                serial_number=device.serial_number,
                location_building=device.building,
                staff_email=row.staff_email,
                staff_employee_id=row.staff_employee_id,
                external_identity_used=normalized.external_identity_used,
                external_identity_type=batch.identity_type if row.staff_email else None,
                authentication_method=normalized.authentication_method,
                activity_type=normalized.activity_type,
                page_count=normalized.page_count,
                sheet_count=normalized.sheet_count,
                color_page_count=normalized.color_page_count,
                monochrome_page_count=normalized.monochrome_page_count,
                duplex=normalized.duplex,
                paper_size=normalized.paper_size,
                occurred_at=normalized.occurred_at,
                period_start=normalized.period_start,
                period_end=normalized.period_end,
                source_connector="generic_csv",
                import_batch_id=batch.id,
                raw_payload=normalized.raw_payload,
            )
        )
        imported_count += 1

    batch.imported_row_count = imported_count
    batch.duplicate_row_count = parsed.duplicate_rows
    batch.unmapped_identity_count = parsed.unmapped_rows
    batch.row_count = parsed.total_rows
    batch.error_detail = [
        {"row_number": r.row_number, "message": r.error} for r in parsed.rows if r.error
    ] or None
    batch.status = "committed"
    batch.committed_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(batch)
    return batch


# ---- History ----


@router.get("/batches", response_model=list[CopierImportBatchOut])
async def list_import_batches(
    mfp_device_id: UUID | None = None, db: AsyncSession = Depends(get_db)
):
    stmt = select(CopierImportBatch)
    if mfp_device_id is not None:
        stmt = stmt.where(CopierImportBatch.mfp_device_id == mfp_device_id)
    result = await db.execute(stmt.order_by(CopierImportBatch.created_at.desc()))
    return result.scalars().all()


@router.get("/batches/{batch_id}", response_model=CopierImportBatchOut)
async def get_import_batch(batch_id: UUID, db: AsyncSession = Depends(get_db)):
    return await _get_batch_or_404(batch_id, db)


@router.delete("/batches/{batch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_import_batch(batch_id: UUID, db: AsyncSession = Depends(get_db)):
    batch = await _get_batch_or_404(batch_id, db)
    if batch.status == "committed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A committed batch can't be deleted (its usage records would be orphaned).",
        )
    Path(batch.raw_file_path).unlink(missing_ok=True)
    await db.delete(batch)
    await db.commit()
