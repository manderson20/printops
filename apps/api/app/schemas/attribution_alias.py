from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class AttributionAliasCreate(BaseModel):
    alias: str
    resolved_email: str
    note: str | None = None


class AttributionAliasOut(BaseModel):
    id: UUID
    alias: str
    resolved_email: str
    source: str
    note: str | None
    created_at: datetime
    updated_at: datetime
    backfilled_job_count: int = 0


class AttributionAliasPage(BaseModel):
    items: list[AttributionAliasOut]
    total: int
    page: int
    page_size: int
