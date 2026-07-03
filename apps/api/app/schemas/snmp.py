from datetime import date
from typing import Literal

from pydantic import BaseModel

SnmpVersion = Literal["v1", "v2c"]
VendorProfile = Literal["canon", "konica_minolta", "hp", "lexmark", "kyocera", "generic"]


class SnmpDefaultsOut(BaseModel):
    """Never returns the decrypted community string — has_community
    indicates whether one's set, matching MosyleSettingsOut's
    has_access_token masking pattern."""

    version: SnmpVersion
    port: int
    has_community: bool
    enabled: bool
    retention_days: int


class SnmpDefaultsUpdate(BaseModel):
    version: SnmpVersion | None = None
    port: int | None = None
    community: str | None = None
    enabled: bool | None = None
    retention_days: int | None = None


class DailyCounterDeltaOut(BaseModel):
    """One day's usage — omitted entirely (not sent as a zero) for a day
    with no SNMP reading at all; a field is null specifically when a
    counter reset/wraparound made that day's delta unavailable (see
    app/printers/counter_history.py:_field_delta)."""

    bucket_start: date
    total_delta: int | None
    copy_delta: int | None
    print_delta: int | None
