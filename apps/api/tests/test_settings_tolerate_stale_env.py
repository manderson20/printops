"""A deployed .env that has drifted ahead of the code must not stop the API.

pydantic-settings ignores unknown *environment variables* but rejects unknown
*dotenv entries* — a distinction that is easy to miss and was missed here. 0.75.8
removed the `redis_url` setting while every deployed `.env` still carried
`PRINTOPS_REDIS_URL`, because it had shipped in `.env.example`. Without
`extra="ignore"` that combination raises at import time, so the API would not
boot and the scheduled updater would fail at the migration step, on every
install at once.

Config files outlive the code that read them. A key nobody uses any more is a
normal state for a deployed file to be in and must be survivable; a *missing
required* setting is the failure that still has to be loud, which is what the
second test pins.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

REQUIRED = {
    "PRINTOPS_JWT_SECRET": "s",
    "PRINTOPS_DATABASE_URL": "postgresql+asyncpg://u:p@localhost/d",
    "PRINTOPS_BACKEND_TOKEN": "t",
    "PRINTOPS_ENCRYPTION_KEY": "k",
}


def _write_env(tmp_path, entries):
    env = tmp_path / ".env"
    env.write_text("".join(f"{k}={v}\n" for k, v in entries.items()))
    return env


def test_a_setting_the_code_no_longer_has_is_ignored(tmp_path, monkeypatch):
    """PRINTOPS_REDIS_URL is the real case: removed from the code in 0.75.8,
    still present in every .env written from the old .env.example."""
    _write_env(tmp_path, {**REQUIRED, "PRINTOPS_REDIS_URL": "redis://localhost:6379/0"})
    monkeypatch.chdir(tmp_path)
    # conftest exports these for the suite, and real environment variables win
    # over the dotenv file — clearing them makes the file the thing under test.
    for key in REQUIRED:
        monkeypatch.delenv(key, raising=False)

    settings = Settings()

    assert settings.backend_token == "t"
    assert not hasattr(settings, "redis_url")


def test_a_missing_required_setting_still_fails(tmp_path, monkeypatch):
    """extra="ignore" must not soften this. Tolerating keys the code dropped is
    not the same as tolerating a config that cannot work."""
    missing = {k: v for k, v in REQUIRED.items() if k != "PRINTOPS_ENCRYPTION_KEY"}
    _write_env(tmp_path, missing)
    monkeypatch.chdir(tmp_path)
    for key in REQUIRED:
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValidationError, match="encryption_key"):
        Settings()
