from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class UnmappedIdentityGroupOut(BaseModel):
    """One distinct (device, raw identity) pair with no resolved staff
    member yet — grouped across every CopierUsageRecord row that shares
    it, since the same unresolved code often recurs across multiple
    import periods before someone notices and maps it."""

    mfp_device_id: UUID
    external_identity_used: str
    occurrence_count: int
    first_seen: datetime
    last_seen: datetime
    attempted_identity_type: str | None
    sample_raw_payload: dict


class ResolveUnmappedRequest(BaseModel):
    mfp_device_id: UUID | None = None
    identity_type: str
    identity_value: str
    resolved_email: str
    note: str | None = None


class ResolveUnmappedOut(BaseModel):
    resolved_email: str
    identity_type: str
    identity_value: str
    mfp_device_id: UUID | None
    backfilled_row_count: int
