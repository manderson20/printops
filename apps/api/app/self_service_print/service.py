"""Self-service web upload printing — a logged-in user uploads a document
and picks a printer, instead of going through a client-configured CUPS/
AirPrint queue. Deliberately reuses the printer's *normal* queue and the
same real CUPS backend/job-logging/attribution path every other job takes
(app/printers/test_print.py's lp submission is the template) — no new
delivery mechanism, no release-queue involvement.

Access control here (which printers a given user may target) is new and
specific to this feature — see app/models/printer_ou_access.py's docstring
for why normal AirPrint/MDM printing is deliberately untouched."""

import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.google_workspace import org_unit_matches
from app.models.google_workspace import GoogleWorkspaceUser
from app.models.printer import Printer
from app.models.printer_ou_access import PrinterAllowedOu

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
SUBMIT_TIMEOUT_SECONDS = 30


class SelfServicePrintError(Exception):
    pass


async def get_allowed_printers_for_user(db: AsyncSession, user_email: str) -> list[Printer]:
    """Every active printer with no PrinterAllowedOu rows (open to
    everyone), plus any restricted printer whose allowed OUs include (or
    are an ancestor of, via org_unit_matches) this user's own OU. A user
    with no synced roster entry (no GoogleWorkspaceUser row — e.g. the dev
    break-glass account) only sees unrestricted printers, same as anyone
    whose OU doesn't match any restricted printer."""
    roster_match = await db.execute(
        select(GoogleWorkspaceUser.org_unit_path).where(GoogleWorkspaceUser.email == user_email)
    )
    user_ou = roster_match.scalar_one_or_none()

    printers_result = await db.execute(
        select(Printer).where(Printer.archived_at.is_(None)).order_by(Printer.name)
    )
    printers = printers_result.scalars().all()

    allowed_result = await db.execute(select(PrinterAllowedOu))
    allowed_ous_by_printer: dict = {}
    for row in allowed_result.scalars().all():
        allowed_ous_by_printer.setdefault(row.printer_id, []).append(row.ou_path)

    return [
        printer
        for printer in printers
        if printer.id not in allowed_ous_by_printer
        or (
            user_ou is not None
            and any(
                org_unit_matches(user_ou, allowed_ou)
                for allowed_ou in allowed_ous_by_printer[printer.id]
            )
        )
    ]


async def user_may_print_to(db: AsyncSession, printer_id, user_email: str) -> bool:
    """Server-side re-check for POST /self-service-print — the picker above
    is a convenience filter, not the actual gate; a client requesting a
    printer_id it was never shown must still be rejected."""
    allowed = await get_allowed_printers_for_user(db, user_email)
    return any(printer.id == printer_id for printer in allowed)


def submit_uploaded_print_job(
    printer_id: str, file_bytes: bytes, filename: str, user_email: str, copies: int
) -> str:
    """Delivers straight to the printer's normal queue via `lp`, same
    "shell out to lp" primitive as app/printers/test_print.py:
    submit_test_print and app/printers/release.py:submit_released_job —
    goes through the real printops CUPS backend (infra/cups/backends/
    printops), so it's logged/attributed exactly like any other job.
    Raises SelfServicePrintError on failure."""
    queue_name = f"printops-{printer_id}"

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(file_bytes)
        path = Path(f.name)

    try:
        result = subprocess.run(
            [
                "lp",
                "-d",
                queue_name,
                "-U",
                user_email,
                "-t",
                filename,
                "-n",
                str(copies),
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=SUBMIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise SelfServicePrintError(
            "The `lp` command isn't available on the PrintOps server."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SelfServicePrintError("Submitting the print job timed out.") from exc
    finally:
        path.unlink(missing_ok=True)

    if result.returncode != 0:
        reason = (result.stderr or result.stdout).strip()
        if "Unknown destination" in reason or "does not exist" in reason:
            raise SelfServicePrintError(
                "No CUPS queue exists for this printer yet — contact an admin."
            )
        raise SelfServicePrintError(reason or "lp exited with an error.")

    return result.stdout.strip()
