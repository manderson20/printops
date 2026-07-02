from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.routers import auth, health, internal, jobs, printers

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description="PrintOps backend API — enterprise print management platform.",
    version="0.1.0",
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
