from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "PrintOps API"
    environment: str = "development"

    cors_origins: list[str] = ["http://localhost:3000"]

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60

    dev_username: str = "admin"
    dev_password: str = "changeme"

    database_url: str

    # LAN address clients (and MDM profiles, e.g. Mosyle) print to directly —
    # this is the CUPS server itself (matches cupsd.conf's Listen directive),
    # not the reverse-proxied public web domain. Defaults to this box's
    # current LAN IP; override via PRINTOPS_PRINT_SERVER_HOST if it changes.
    print_server_host: str = "172.16.2.10"
    print_server_port: int = 631

    # Shared secret for service-to-service calls (the CUPS backend script),
    # separate from user JWT auth — see app/deps.py's verify_backend_token.
    backend_token: str

    # Reserved for future use — not connected to anything yet in this scaffold.
    redis_url: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_prefix="PRINTOPS_")


@lru_cache
def get_settings() -> Settings:
    return Settings()
