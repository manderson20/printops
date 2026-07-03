import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.core.config import get_settings
from app.db import AsyncSessionLocal
from app.integrations.git_update import get_current_version
from app.integrations.google_workspace import GoogleWorkspaceError
from app.integrations.google_workspace import run_sync as run_google_workspace_sync
from app.integrations.mosyle import MosyleError
from app.integrations.mosyle import run_sync as run_mosyle_sync
from app.models.google_workspace import GoogleWorkspaceSettings
from app.models.mosyle import MosyleSettings
from app.models.printer import Printer
from app.printers.status import refresh_printer_status
from app.routers import (
    auth,
    device_overrides,
    health,
    internal,
    jobs,
    printers,
    reports,
    settings as settings_router,
    updates,
    users,
)

settings = get_settings()
logger = logging.getLogger(__name__)

DEVICE_SYNC_INTERVAL_SECONDS = 15 * 60


def _make_device_sync_loop(name: str, settings_model, run_sync_fn, error_cls: type[Exception]):
    """Builds a periodic device-cache sync loop for a device→user
    integration (Mosyle, Google Workspace, ...) — refreshed periodically
    so per-job attribution lookups (app/attribution/resolve.py) never make
    a live API call. Runs forever until cancelled at shutdown; a sync
    failure just logs and retries next interval rather than crashing."""

    async def _loop() -> None:
        while True:
            try:
                async with AsyncSessionLocal() as db:
                    result = await db.execute(select(settings_model.enabled).limit(1))
                    if result.scalar_one_or_none():
                        await run_sync_fn(db)
            except error_cls as exc:
                logger.warning("%s device sync failed: %s", name, exc)
            except Exception:
                logger.exception("Unexpected error in %s device sync loop", name)
            await asyncio.sleep(DEVICE_SYNC_INTERVAL_SECONDS)

    return _loop


PRINTER_STATUS_POLL_INTERVAL_SECONDS = 60


async def _printer_status_poll_loop() -> None:
    """Refreshes every printer's online/error/offline status (see
    app/printers/status.py) once a minute — frequent enough to catch a jam
    or an offline printer quickly without hammering the fleet. Each printer
    is probed independently (asyncio.gather + return_exceptions) so one
    unreachable printer can't stall/skip the rest of the cycle; a cycle-level
    failure (e.g. DB down) just logs and retries next interval."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                printers = (await db.execute(select(Printer))).scalars().all()

                async def _refresh_one(printer: Printer) -> None:
                    await refresh_printer_status(printer)

                await asyncio.gather(*(_refresh_one(p) for p in printers), return_exceptions=True)
                await db.commit()
        except Exception:
            logger.exception("Unexpected error in printer status poll loop")
        await asyncio.sleep(PRINTER_STATUS_POLL_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [
        asyncio.create_task(_make_device_sync_loop("Mosyle", MosyleSettings, run_mosyle_sync, MosyleError)()),
        asyncio.create_task(
            _make_device_sync_loop(
                "Google Workspace", GoogleWorkspaceSettings, run_google_workspace_sync, GoogleWorkspaceError
            )()
        ),
        asyncio.create_task(_printer_status_poll_loop()),
    ]
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title=settings.app_name,
    description="PrintOps backend API — enterprise print management platform.",
    version=get_current_version(),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(printers.router, prefix="/api/v1/printers", tags=["printers"])
app.include_router(jobs.router, prefix="/api/v1/jobs", tags=["jobs"])
app.include_router(jobs.user_router, prefix="/api/v1/jobs", tags=["jobs"])
app.include_router(internal.router, prefix="/api/v1/internal", tags=["internal"])
app.include_router(settings_router.router, prefix="/api/v1/settings", tags=["settings"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(device_overrides.router, prefix="/api/v1/devices", tags=["devices"])
app.include_router(updates.router, prefix="/api/v1/updates", tags=["updates"])
app.include_router(reports.router, prefix="/api/v1/reports", tags=["reports"])
