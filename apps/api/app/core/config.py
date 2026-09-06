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

    # Whether /docs, /redoc and /openapi.json are served at all.
    #
    # Off by default because this host answers from the public internet
    # and those three describe every endpoint, every schema and every
    # parameter the API has, to anyone who finds the hostname. That is a
    # map for somebody deciding what to try, and it was reachable without
    # signing in.
    #
    # Not gated behind admin auth instead: the interactive docs are a
    # browser page and the API authenticates with a bearer token, so a
    # signed-in admin's browser has no way to present one. A setting that
    # turns them on for a development box is the honest shape.
    expose_api_docs: bool = False

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

    # Fernet key for encrypting third-party integration credentials at rest
    # (e.g. Mosyle admin password/API token, entered via the web UI and
    # stored in Postgres — see app/core/crypto.py). Generate with:
    # python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: str

    model_config = SettingsConfigDict(env_file=".env", env_prefix="PRINTOPS_")


@lru_cache
def get_settings() -> Settings:
    return Settings()
