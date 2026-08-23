import os

os.environ.setdefault("PRINTOPS_JWT_SECRET", "test-secret")
os.environ.setdefault("PRINTOPS_DEV_USERNAME", "admin")
os.environ.setdefault("PRINTOPS_DEV_PASSWORD", "changeme")
# Unused directly by tests (test_printers_api overrides get_db with SQLite), but
# Settings() requires it and app.main builds a module-level Settings on import.
os.environ.setdefault(
    "PRINTOPS_DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost/unused"
)
os.environ.setdefault("PRINTOPS_BACKEND_TOKEN", "test-backend-token")
# Fernet key — must be 32 url-safe base64-encoded bytes. Fixed test value so
# runs are reproducible; production generates its own via
# `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
os.environ.setdefault("PRINTOPS_ENCRYPTION_KEY", "zovsKJRTibYW7qfTSaEux7Pz22nKwCqH2AhB6M0DuDU=")

# --- per-test isolation of the status poll's in-process state ---
#
# The poll keeps per-printer state in module-level dicts that deliberately live
# for the lifetime of the process: the queue-stall clock and the
# queue-recovery attempt record (app/printers/queue_stall.py,
# queue_recovery.py). Both explain why in their own docstrings — a verdict this
# process did not observe is not one it can honestly report, so neither
# survives a restart. (The network-flap window used to be here too and is now a
# column on the printer row, for the reason migration 0063 gives.)
#
# A test run has no restart, so that state leaks between tests, and it leaks as
# a test passing for a reason unrelated to what it asserts. The network-flap
# window found this the moment it was added: misses accumulated across four
# earlier tests in one file were enough to trip an alarm inside a fifth that was
# only checking a status field.

import pytest

from app.printers import queue_recovery, queue_stall


@pytest.fixture(autouse=True)
def _clear_poll_state():
    """Start every test with the blank per-printer state a freshly started API
    process would have, and leave none behind."""
    for module in (queue_stall, queue_recovery):
        module.reset()
    yield
    for module in (queue_stall, queue_recovery):
        module.reset()
