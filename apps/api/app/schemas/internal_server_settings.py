from pydantic import BaseModel


class InternalServerSettingsOut(BaseModel):
    """What scripts/sync_server_settings.sh actually needs — deliberately
    smaller than ServerSettingsOut (no certificate status, no sync_error;
    the script is the thing that PRODUCES those, not a consumer of them)."""

    hostname: str
    require_encryption: bool
    advertise_ipps: bool


class InternalPrinterIdOut(BaseModel):
    id: str
