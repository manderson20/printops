import pytest

from app.copiers.device_admin import (
    DeviceAdminCredentials,
    DeviceCredentialsMissing,
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
