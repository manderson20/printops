from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class LdapRelaySettingsOut(BaseModel):
    enabled: bool
    base_dn: str
    port: int


class LdapRelaySettingsUpdate(BaseModel):
    enabled: bool | None = None
    base_dn: str | None = None
    port: int | None = None


class LdapBindRequest(BaseModel):
    """Whatever bind identifier the copier sent (vendors format this field
    differently — a bare username, or a full DN some copiers construct
    themselves) — matched against Printer.ldap_bind_username, not parsed as
    a strict DN. See app/routers/internal.py:ldap_bind."""

    bind_identifier: str
    password: str


class LdapBindResult(BaseModel):
    success: bool
    printer_id: UUID | None = None


class LdapSearchRequest(BaseModel):
    filter_attr: Literal["cn", "mail"]
    filter_type: Literal["equality", "substring"]
    filter_value: str


class LdapEntryOut(BaseModel):
    dn: str
    cn: str
    mail: str
    employee_number: str | None


class LdapSearchResult(BaseModel):
    entries: list[LdapEntryOut]
