from pydantic import BaseModel


class ZabbixSettingsUpdate(BaseModel):
    enabled: bool | None = None
    base_url: str | None = None


class ZabbixSettingsOut(BaseModel):
    """api_token is always returned in full, never masked — same
    convention as PrinterOut.release_token: a capability token meant to
    be copied into a Zabbix host macro, not a login credential."""

    enabled: bool
    api_token: str | None
    base_url: str | None


class ZabbixSummaryOut(BaseModel):
    """Rolling-24h fleet totals — see zabbix_integration.py's summary
    endpoint docstring for why rolling, not wall-clock "today"."""

    total_jobs: int
    forwarded_jobs: int
    failed_jobs: int
    cancelled_jobs: int
    total_pages: int
    color_pages: int
    mono_pages: int
    duplex_pages: int
    simplex_pages: int


class ZabbixPrinterDetailOut(BaseModel):
    """Flattened for easy Zabbix JSONPath preprocessing — nullable
    Printer fields become "" here (except page_count_total, which stays
    genuinely null when unpolled so a Zabbix item goes "not supported"
    rather than recording a false zero)."""

    status: str
    status_reasons: str
    queue_sync_error: str
    page_count_total: int | None
    page_count_confidence: str
    building: str
    room: str
    department: str
