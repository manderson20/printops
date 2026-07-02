import os

os.environ.setdefault("PRINTOPS_JWT_SECRET", "test-secret")
os.environ.setdefault("PRINTOPS_DEV_USERNAME", "admin")
os.environ.setdefault("PRINTOPS_DEV_PASSWORD", "changeme")
# Unused directly by tests (test_printers_api overrides get_db with SQLite), but
# Settings() requires it and app.main builds a module-level Settings on import.
os.environ.setdefault("PRINTOPS_DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost/unused")
