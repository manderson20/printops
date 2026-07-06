from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.db import get_db
from app.deps import verify_backend_token
from app.ldap_relay.service import get_or_create_ldap_relay_settings
from app.models.google_workspace import GoogleWorkspaceUser
from app.models.printer import Printer
from app.schemas.ldap_relay import (
    LdapBindRequest,
    LdapBindResult,
    LdapEntryOut,
    LdapRelaySettingsOut,
    LdapSearchRequest,
    LdapSearchResult,
)
from app.schemas.printer import PrinterConnectionOut

router = APIRouter(dependencies=[Depends(verify_backend_token)])

# Sizelimit for one search response — an address-book type-ahead search
# never needs more than this many candidates, and it keeps one query from
# ever pulling the entire roster over the wire in one response.
LDAP_SEARCH_SIZE_LIMIT = 200


@router.get("/printers/{printer_id}/connection", response_model=PrinterConnectionOut)
async def get_printer_connection(printer_id: UUID, db: AsyncSession = Depends(get_db)):
    """Called by the CUPS backend script to look up where to forward a job.
    Deliberately separate from the user-facing printers router — different
    trust boundary (service-to-service, not a logged-in admin)."""
    printer = await db.get(Printer, printer_id)
    if printer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Printer not found")
    return printer


@router.get("/ldap/settings", response_model=LdapRelaySettingsOut)
async def get_ldap_relay_settings_internal(db: AsyncSession = Depends(get_db)):
    """Called by the LDAP relay service at startup and for its RootDSE
    response — same data as the admin-facing GET /api/v1/settings/ldap,
    just reachable via the backend-token trust boundary instead of a JWT."""
    settings = await get_or_create_ldap_relay_settings(db)
    return LdapRelaySettingsOut(enabled=settings.enabled, base_dn=settings.base_dn, port=settings.port)


@router.post("/ldap/bind", response_model=LdapBindResult)
async def ldap_bind(payload: LdapBindRequest, db: AsyncSession = Depends(get_db)):
    """Called by the LDAP relay service (infra/ldap-relay/) for every bind
    attempt a copier makes. Deliberately always returns a plain
    success/failure result (never 404/401) — a bind failure is an expected,
    routine outcome here, not an error condition for this internal caller."""
    settings = await get_or_create_ldap_relay_settings(db)
    if not settings.enabled:
        return LdapBindResult(success=False)

    identifier = payload.bind_identifier.strip().lower()
    if not identifier:
        return LdapBindResult(success=False)

    result = await db.execute(
        select(Printer).where(func.lower(Printer.ldap_bind_username) == identifier)
    )
    printer = result.scalar_one_or_none()
    if (
        printer is None
        or not printer.ldap_enabled
        or not printer.ldap_bind_password_hash
        or not verify_password(payload.password, printer.ldap_bind_password_hash)
    ):
        return LdapBindResult(success=False)

    return LdapBindResult(success=True, printer_id=printer.id)


@router.post("/ldap/search", response_model=LdapSearchResult)
async def ldap_search(payload: LdapSearchRequest, db: AsyncSession = Depends(get_db)):
    """Called by the LDAP relay service for every search a bound copier
    issues — serves address-book entries straight from the already-synced
    Google Workspace roster (app/integrations/google_workspace.py), never a
    live Google call. Returns an empty result (not an error) whenever the
    relay is disabled or nothing matches, matching ldap_bind's "routine
    outcome" convention above."""
    settings = await get_or_create_ldap_relay_settings(db)
    if not settings.enabled:
        return LdapSearchResult(entries=[])

    value = payload.filter_value.strip()
    if not value:
        return LdapSearchResult(entries=[])

    column = GoogleWorkspaceUser.name if payload.filter_attr == "cn" else GoogleWorkspaceUser.email
    condition = (
        func.lower(column) == value.lower()
        if payload.filter_type == "equality"
        else column.ilike(f"%{value}%")
    )

    result = await db.execute(
        select(GoogleWorkspaceUser)
        .where(column.is_not(None), condition)
        .order_by(GoogleWorkspaceUser.email)
        .limit(LDAP_SEARCH_SIZE_LIMIT)
    )
    entries = [
        LdapEntryOut(
            dn=f"mail={user.email},ou=people,{settings.base_dn}",
            cn=user.name or user.email,
            mail=user.email,
            employee_number=user.employee_id,
        )
        for user in result.scalars().all()
    ]
    return LdapSearchResult(entries=entries)
