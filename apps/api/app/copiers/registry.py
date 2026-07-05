"""Single source of truth for which connectors actually exist. The
frontend's connector-type picker (apps/web/.../mfp-devices/new/page.tsx)
lists only these keys — an unimplemented vendor connector (Konica bizhub,
Lexmark, HP, ...) simply isn't selectable yet, rather than existing as a
fake option that raises NotImplementedError at runtime. See
app/copiers/connector.py's module docstring for why."""

from app.copiers.canon_department_id import CanonDepartmentIdConnector
from app.copiers.connector import CopierConnector
from app.copiers.generic_csv import GenericCsvConnector
from app.copiers.generic_snmp import GenericSnmpConnector

CONNECTOR_REGISTRY: dict[str, type[CopierConnector]] = {
    "generic_csv": GenericCsvConnector,
    "generic_snmp": GenericSnmpConnector,
    "canon_department_id": CanonDepartmentIdConnector,
}


def get_connector(connector_type: str) -> CopierConnector:
    connector_cls = CONNECTOR_REGISTRY.get(connector_type)
    if connector_cls is None:
        raise ValueError(f"Unknown connector_type: {connector_type!r}")
    return connector_cls()
