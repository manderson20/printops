"""Resolving a device's admin web UI credentials for connectors that
actually log into the device.

One place, rather than each vendor connector reaching for
MfpDevice.admin_password_encrypted and calling decrypt() itself — the
failure mode when a credential is missing should read the same whichever
vendor hit it, and the "never log the password" rule is easier to keep
when only one module ever holds the plaintext.

Why a dedicated exception rather than returning None: "no credential
configured" is an admin-fixable setup problem (a 4xx the router explains),
not a device/network failure (SnmpProbeError) and not a structural
"this connector can't do that" (CapabilityNotSupported). Callers that
conflate the three produce misleading errors — the same reasoning
app/copiers/connector.py's module docstring gives for keeping
CapabilityNotSupported distinct from a probe error.
"""

from dataclasses import dataclass

from app.core.crypto import decrypt
from app.integrations.google_workspace import org_unit_matches
from app.models.mfp_device import MfpDevice


class DeviceCredentialsMissing(Exception):
    """The device has no admin password stored, so a connector method that
    must log in can't run. Routers translate this to a 4xx telling the
    admin which device needs a credential — never a 500."""


@dataclass(frozen=True)
class DeviceAdminCredentials:
    """username is "" (not None) when the vendor's admin login has no
    username concept — Konica bizhub and Lexmark XM3350 both post an empty
    username field rather than omitting it, so the empty string is the
    correct wire value, and connectors shouldn't each re-derive that."""

    username: str
    password: str

    def __repr__(self) -> str:  # pragma: no cover - trivial
        """Redacted so the password can't reach a log or traceback through
        an accidental repr() of this object."""
        return f"DeviceAdminCredentials(username={self.username!r}, password=<redacted>)"


def device_provisioning_scope(
    device: MfpDevice, org_wide_includes: list[str], org_wide_excludes: list[str]
) -> tuple[list[str], list[str]]:
    """The (include, exclude) OU lists that decide which staff get accounts
    on THIS device — the org-wide copier-accounting scope
    (GoogleWorkspaceSettings.copier_identity_org_unit_paths), narrowed by
    the device's own provision_org_unit_paths.

    Narrowed, never widened: a device list is intersected with the org-wide
    one by keeping only device OUs that the org-wide filter already allows,
    so pointing a copier at an OU the org doesn't track for copier
    accounting can't quietly re-admit it. Org-wide excludes always still
    apply.

    An empty/None device list means "no per-device narrowing" — the
    org-wide scope is used as-is. That is deliberately different from an
    empty result, which means "this device provisions nobody"."""
    device_paths = [p for p in (device.provision_org_unit_paths or []) if p]
    if not device_paths:
        return list(org_wide_includes), list(org_wide_excludes)
    if not org_wide_includes:
        # No org-wide include filter means "everyone"; the device list is
        # then the whole constraint.
        return device_paths, list(org_wide_excludes)
    allowed = [
        path
        for path in device_paths
        if any(org_unit_matches(path, allowed_path) for allowed_path in org_wide_includes)
    ]
    return allowed, list(org_wide_excludes)


def get_admin_credentials(device: MfpDevice) -> DeviceAdminCredentials:
    if not device.admin_password_encrypted:
        raise DeviceCredentialsMissing(
            f"{device.name} has no admin password stored. Add one on the device's "
            "page in PrintOps before running this."
        )
    return DeviceAdminCredentials(
        username=device.admin_username or "",
        password=decrypt(device.admin_password_encrypted),
    )
