import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.core.config import get_settings
from app.db import AsyncSessionLocal
from app.integrations.mosyle import MosyleError, run_sync
from app.models.mosyle import MosyleSettings
from app.routers import auth, health, internal, jobs, printers, settings as settings_router

settings = get_settings()
logger = logging.getLogger(__name__)

MOSYLE_SYNC_INTERVAL_SECONDS = 15 * 60


async def _mosyle_sync_loop() -> None:
    """Refreshes the Mosyle device cache periodically so per-job attribution
    lookups (app/attribution/resolve.py) never make a live API call — see
    ARCHITECTURE.md's Mosyle section. Runs forever until cancelled at
    shutdown; a sync failure just logs and retries next interval rather
    than crashing the loop."""
    while True:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(MosyleSettings.enabled).limit(1))
                enabled = result.scalar_one_or_none()
                if enabled:
                    await run_sync(db)
        except MosyleError as exc:
            logger.warning("Mosyle device sync failed: %s", exc)
        except Exception:
            logger.exception("Unexpected error in Mosyle device sync loop")
        await asyncio.sleep(MOSYLE_SYNC_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_mosyle_sync_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title=settings.app_name,
    description="PrintOps backend API — enterprise print management platform.",
    version="0.1.0",
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
