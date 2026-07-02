import os

os.environ.setdefault("PRINTOPS_JWT_SECRET", "test-secret")
os.environ.setdefault("PRINTOPS_DEV_USERNAME", "admin")
os.environ.setdefault("PRINTOPS_DEV_PASSWORD", "changeme")
# Unused directly by tests (test_printers_api overrides get_db with SQLite), but
# Settings() requires it and app.main builds a module-level Settings on import.
os.environ.setdefault("PRINTOPS_DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost/unused")
os.environ.setdefault("PRINTOPS_BACKEND_TOKEN", "test-backend-token")
# Fernet key — must be 32 url-safe base64-encoded bytes. Fixed test value so
# runs are reproducible; production generates its own via
# `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
os.environ.setdefault("PRINTOPS_ENCRYPTION_KEY", "zovsKJRTibYW7qfTSaEux7Pz22nKwCqH2AhB6M0DuDU=")
