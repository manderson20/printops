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


class SnmpDefaultsUpdate(BaseModel):
    version: SnmpVersion | None = None
    port: int | None = None
    community: str | None = None
    enabled: bool | None = None
