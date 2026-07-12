from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import get_current_user
from app.models.printer import Printer
from app.schemas.auth import UserOut
from app.schemas.self_service_print import SelfServicePrinterOut, SelfServicePrintResultOut
from app.self_service_print.service import (
    MAX_UPLOAD_BYTES,
    SelfServicePrintError,
    get_allowed_printers_for_user,
    submit_uploaded_print_job,
    user_may_print_to,
)

router = APIRouter(dependencies=[Depends(get_current_user)])


def _require_email(current_user: UserOut) -> str:
    """The dev break-glass account (app/routers/auth.py's /auth/login) has
    no email — it isn't a real Google Workspace identity, so it has
    nothing to attribute a self-service job to and no roster OU to check
    against. Real SSO users always have one."""
    if not current_user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Self-service printing requires a Google Workspace account.",
        )
    return current_user.email


@router.get("/printers", response_model=list[SelfServicePrinterOut])
async def list_self_service_printers(
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
):
    email = _require_email(current_user)
    return await get_allowed_printers_for_user(db, email)


@router.post("", response_model=SelfServicePrintResultOut, status_code=status.HTTP_201_CREATED)
async def submit_self_service_print(
    printer_id: UUID = Form(...),
    copies: int = Form(1),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: UserOut = Depends(get_current_user),
):
    email = _require_email(current_user)

    if copies < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Copies must be at least 1."
        )

    # Re-checked server-side — the printer picker (GET /printers above) is
    # a convenience filter for the UI, not the actual access-control gate.
    if not await user_may_print_to(db, printer_id, email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You aren't allowed to print to this printer.",
        )

    raw_bytes = await file.read()
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit.",
        )
    # PDF-only for v1 — universally supported via CUPS's driverless filter
    # chain, avoids format-conversion complexity. Checked by content, not
    # just the client-supplied filename/content-type, neither of which are
    # trustworthy.
    if not raw_bytes.startswith(b"%PDF-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are supported.",
        )

    printer = await db.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Printer not found")

    try:
        submit_uploaded_print_job(
            str(printer_id), raw_bytes, file.filename or "document.pdf", email, copies
        )
    except SelfServicePrintError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return SelfServicePrintResultOut(
        printer_id=printer_id,
        printer_name=printer.name,
        filename=file.filename or "document.pdf",
        copies=copies,
    )
