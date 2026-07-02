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

    # Reserved for future use — not connected to anything yet in this scaffold.
    redis_url: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_prefix="PRINTOPS_")


@lru_cache
def get_settings() -> Settings:
    return Settings()
