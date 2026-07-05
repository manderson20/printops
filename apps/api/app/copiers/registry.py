"""Single source of truth for which connectors actually exist. The
frontend's connector-type picker (apps/web/.../mfp-devices/new/page.tsx)
lists only these keys — an unimplemented vendor connector simply isn't
selectable, rather than existing as a fake option that raises
NotImplementedError at runtime. See app/copiers/connector.py's module
docstring for why.

Canon, Konica Minolta, Kyocera, Ricoh, and Xerox each have a real
named-feature connector — a well-documented, stable, device-local
accounting feature this codebase can confidently describe (Canon
Department ID Management, Konica Account Track/User Authentication,
Kyocera Job Accounting/User Login, Ricoh User Code Authentication, Xerox
Standard Accounting). Lexmark/HP/Sharp are Stage 4 *placeholders*
(app/copiers/vendor_placeholders.py) — registered and selectable, but
deliberately not full connectors, because their real solutions either
require separate server software (HP Access Control/JetAdvantage,
Lexmark Print Management) or vary too much across firmware generations
to name with confidence (Sharp); see that module's docstring for
details."""

from app.copiers.canon_department_id import CanonDepartmentIdConnector
from app.copiers.connector import CopierConnector
from app.copiers.generic_csv import GenericCsvConnector
from app.copiers.generic_snmp import GenericSnmpConnector
from app.copiers.konica_bizhub import KonicaBizhubConnector
from app.copiers.kyocera_department_management import KyoceraDepartmentManagementConnector
from app.copiers.ricoh_user_code_auth import RicohUserCodeAuthConnector
from app.copiers.vendor_placeholders import (
    HpAccessControlConnector,
    LexmarkAccountingConnector,
    SharpAccountingConnector,
)
from app.copiers.xerox_standard_accounting import XeroxStandardAccountingConnector

CONNECTOR_REGISTRY: dict[str, type[CopierConnector]] = {
    "generic_csv": GenericCsvConnector,
    "generic_snmp": GenericSnmpConnector,
    "canon_department_id": CanonDepartmentIdConnector,
    "konica_bizhub": KonicaBizhubConnector,
    "kyocera_department_management": KyoceraDepartmentManagementConnector,
    "ricoh_user_code_auth": RicohUserCodeAuthConnector,
    "xerox_standard_accounting": XeroxStandardAccountingConnector,
    "lexmark_accounting": LexmarkAccountingConnector,
    "hp_access_control": HpAccessControlConnector,
    "sharp_accounting": SharpAccountingConnector,
}


def get_connector(connector_type: str) -> CopierConnector:
    connector_cls = CONNECTOR_REGISTRY.get(connector_type)
    if connector_cls is None:
        raise ValueError(f"Unknown connector_type: {connector_type!r}")
    return connector_cls()
