import pytest

from app.copiers.device_admin import (
    DeviceAdminCredentials,
    DeviceCredentialsMissing,
    device_provisioning_scope,
    get_admin_credentials,
)
from app.core.crypto import encrypt
from app.models.mfp_device import MfpDevice


def test_returns_decrypted_credentials():
    device = MfpDevice(
        name="HS Copier - Monica",
        admin_username="admin",
        admin_password_encrypted=encrypt("hunter2"),
    )
    creds = get_admin_credentials(device)
    assert creds == DeviceAdminCredentials(username="admin", password="hunter2")


def test_missing_username_becomes_empty_string_not_none():
    """A bizhub's admin login has no username field — the wire value is an
    empty string, so connectors don't each have to handle None."""
    device = MfpDevice(name="ES Veronica Copier", admin_password_encrypted=encrypt("hunter2"))
    assert get_admin_credentials(device).username == ""


def test_missing_password_raises_actionable_error():
    """Distinct from CapabilityNotSupported (structural) and SnmpProbeError
    (device unreachable) — this one is admin-fixable setup, and the message
    has to say which device."""
    device = MfpDevice(name="SS Fax Copier")
    with pytest.raises(DeviceCredentialsMissing) as excinfo:
        get_admin_credentials(device)
    assert "SS Fax Copier" in str(excinfo.value)


def test_repr_does_not_leak_the_password():
    """repr() reaches logs and tracebacks by accident; the password must
    not ride along."""
    creds = DeviceAdminCredentials(username="admin", password="hunter2")
    assert "hunter2" not in repr(creds)
    assert "admin" in repr(creds)


class TestDeviceProvisioningScope:
    """Which staff get accounts on a specific copier — the org-wide
    copier-accounting scope narrowed by the device's own OU list."""

    def test_no_device_list_uses_the_org_wide_scope(self):
        device = MfpDevice(name="HS Copier")
        assert device_provisioning_scope(device, ["/Employees"], ["/Employees/Inactive"]) == (
            ["/Employees"],
            ["/Employees/Inactive"],
        )

    def test_device_list_narrows_to_one_building(self):
        device = MfpDevice(
            name="ES Veronica Copier",
            provision_org_unit_paths=["/Employees/Elementary School"],
        )
        includes, _ = device_provisioning_scope(device, ["/Employees"], [])
        assert includes == ["/Employees/Elementary School"]

    def test_device_cannot_widen_beyond_the_org_wide_scope(self):
        """Pointing a copier at an OU the org doesn't track for copier
        accounting must not quietly re-admit it — students, typically."""
        device = MfpDevice(
            name="ES Veronica Copier",
            provision_org_unit_paths=[
                "/Students/Elementary School",
                "/Employees/Elementary School",
            ],
        )
        includes, _ = device_provisioning_scope(device, ["/Employees"], [])
        assert includes == ["/Employees/Elementary School"]

    def test_org_wide_excludes_always_survive(self):
        device = MfpDevice(name="X", provision_org_unit_paths=["/Employees/High School"])
        _, excludes = device_provisioning_scope(
            device, ["/Employees"], ["/Employees/Inactive Employees"]
        )
        assert excludes == ["/Employees/Inactive Employees"]

    def test_device_list_is_the_whole_constraint_when_org_wide_is_unfiltered(self):
        device = MfpDevice(name="X", provision_org_unit_paths=["/Employees/High School"])
        includes, _ = device_provisioning_scope(device, [], [])
        assert includes == ["/Employees/High School"]

    def test_device_scoped_entirely_outside_the_org_scope_provisions_nobody(self):
        """An empty result means "nobody", which is different from an empty
        device list meaning "no narrowing"."""
        device = MfpDevice(name="X", provision_org_unit_paths=["/Students"])
        includes, _ = device_provisioning_scope(device, ["/Employees"], [])
        assert includes == []
