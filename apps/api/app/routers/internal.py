from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.deps import verify_backend_token
from app.models.printer import Printer
from app.schemas.printer import PrinterConnectionOut

router = APIRouter(dependencies=[Depends(verify_backend_token)])


@router.get("/printers/{printer_id}/connection", response_model=PrinterConnectionOut)
async def get_printer_connection(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    """Called by the CUPS backend script to look up where to forward a job.
    Deliberately separate from the user-facing printers router — different
    trust boundary (service-to-service, not a logged-in admin)."""
    printer = await db.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Printer not found")
    return printer
