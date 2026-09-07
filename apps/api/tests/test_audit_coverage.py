"""Every admin action that changes something must record that it did.

The failure mode this guards is silent and permanent: an endpoint that changes
state and forgets to call record_audit produces no error, no warning and no row.
The log simply never mentions that kind of change, and nobody finds out until
somebody asks the question the log was built to answer.

So it is checked mechanically rather than by memory, the same way
test_everywhere_guard_coverage.py checks that every script guards `-m
everywhere`. Adding a new settings endpoint without auditing it should fail CI
today, not be noticed next year.

Exemptions are listed individually with a reason. A route being absent from both
lists is a failure, so the only way past this test is a deliberate decision that
somebody wrote down.
"""

import ast
from pathlib import Path

import pytest

ROUTERS = Path(__file__).resolve().parents[1] / "app" / "routers"

# Routers whose mutating endpoints are in scope for the audit log. Scope is
# admin and config actions, decided with the district: service-to-service
# traffic (the CUPS backend posting a row per print job, Zabbix polling) and
# ordinary user actions are deliberately outside it. A log dominated by machine
# traffic is one nobody reads, and job history already lives on Job rows.
AUDITED_ROUTERS = (
    "settings.py",
    "users.py",
    "auth.py",
    "printers.py",
    "mfp_devices.py",
    "notifications.py",
)

MUTATING_METHODS = {"post", "put", "patch", "delete"}

RECORDING_CALLS = {"record_audit", "record_settings_update"}

# Handler name -> why it changes state without being audited.
EXEMPT = {
    # Read-only probes. They reach out to a third party and report back; nothing
    # in PrintOps changes, and they are POSTs only because they take a body.
    "test_mosyle_connection": "read-only connection probe, changes nothing",
    "test_classguard_connection": "read-only connection probe, changes nothing",
    "test_google_workspace_connection": "read-only connection probe, changes nothing",
    # Cache refreshes from an external source of truth. These write a lot of
    # rows and none of them are an admin's decision — the admin decided to press
    # sync, and what lands is whatever the third party says. Auditing the button
    # press without the contents would be noise; auditing the contents would be
    # thousands of rows describing someone else's data.
    "sync_mosyle_devices": (
        "refreshes a device cache from Mosyle; contents are not an admin decision"
    ),
    "sync_google_workspace_devices": (
        "refreshes a device cache from Google; same reasoning as Mosyle"
    ),
    "sync_server_settings_now": (
        "re-applies already-audited settings to the host; the change was recorded when it was made"
    ),
    # Authentication endpoints that are not a login. Covered by rate limiting
    # rather than the audit trail.
    "refresh": "reissues a token for an already-authenticated session; no state change",
    "test_channel": (
        "sends one message and records the outcome on the channel; an admin "
        "pressing Test three times while sorting out a URL should not fill the trail"
    ),
    # Probes and re-applications of config that was already recorded when it was
    # set. None of these represent a new decision by an admin.
    "discover_printer": (
        "capability probe against the device; records what the printer says, not a decision"
    ),
    "resync_queue": (
        "re-applies the existing config to CUPS; the config change was recorded when it was made"
    ),
    "check_status": "status probe",
    "check_counters": "SNMP counter probe",
    "detect_toner_cartridges": "cartridge probe against the device",
    "test_print": "sends a page; changes no configuration",
    "test_mfp_device_connection": "read-only connection probe",
    "check_mfp_device_capabilities": "capability probe",
    "check_mfp_device_meter": "meter probe",
    "sync_device_users": (
        "refreshes a user cache from the device; contents are not an admin decision"
    ),
    "poll_device_counters": "counter poll",
    "attribute_device_copies_now": (
        "re-runs attribution over existing records; derives, decides nothing"
    ),
}

# Routes that *should* be audited and are not yet.
#
# Empty, and worth keeping rather than deleting: it is the difference between
# "there is nothing here worth recording" (EXEMPT, above) and "there is, and we
# have not written it". Merging the two would let a real gap dissolve into a
# list of things that are genuinely fine, which is the one outcome this file
# exists to prevent. An entry here is a debt with a name on it.
#
# It held fifteen entries when auditing first shipped — quotas, release
# bypasses, OU restrictions, the toner cost model, token rotation, job purges —
# and they were cleared in 0.76.1.
DEFERRED: dict[str, str] = {}


def _handlers(path: Path) -> list[tuple[str, str]]:
    """(handler name, http method) for every mutating route in a router file."""
    tree = ast.parse(path.read_text())
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            func = call.func if call else decorator
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr in MUTATING_METHODS and isinstance(func.value, ast.Name):
                if func.value.id.endswith("router"):
                    found.append((node.name, func.attr))
    return found


def _records_audit(path: Path, handler: str) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == handler:
            return any(
                isinstance(call.func, ast.Name) and call.func.id in RECORDING_CALLS
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
            )
    return False


def _audited_router_paths() -> list[Path]:
    return [ROUTERS / name for name in AUDITED_ROUTERS]


@pytest.mark.parametrize("router_path", _audited_router_paths(), ids=lambda p: p.name)
def test_every_mutating_admin_route_is_audited_or_exempt(router_path):
    assert router_path.exists(), f"{router_path.name} was renamed — update AUDITED_ROUTERS"

    missing = []
    for handler, method in _handlers(router_path):
        if handler in EXEMPT or handler in DEFERRED:
            continue
        if not _records_audit(router_path, handler):
            missing.append(f"{method.upper()} -> {handler}()")

    assert not missing, (
        f"{router_path.name} has state-changing routes that record no audit event:\n  "
        + "\n  ".join(missing)
        + "\n\nEither call record_audit()/record_settings_update() in the handler, or add it "
        "to EXEMPT in this file with the reason it changes nothing worth recording."
    )


def test_exemptions_all_refer_to_routes_that_still_exist():
    """An exemption for a deleted handler is a stale excuse that would silently
    cover a future function reusing the name."""
    live = {
        handler
        for path in _audited_router_paths()
        if path.exists()
        for handler, _ in _handlers(path)
    }
    stale = sorted((set(EXEMPT) | set(DEFERRED)) - live)
    assert not stale, f"EXEMPT/DEFERRED name handlers that no longer exist: {stale}"


def test_every_exemption_gives_a_reason():
    empty = sorted(name for name, reason in (EXEMPT | DEFERRED).items() if not reason.strip())
    assert not empty, f"exemptions without a reason: {empty}"


def test_nothing_is_both_exempt_and_deferred():
    """The two mean opposite things — "nothing to record here" versus "something
    to record, not written yet". A handler in both would make the gap
    unreadable, which is the one thing this file exists to prevent."""
    both = sorted(set(EXEMPT) & set(DEFERRED))
    assert not both, f"listed as both exempt and deferred: {both}"
