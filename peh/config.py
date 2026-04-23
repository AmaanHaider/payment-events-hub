from __future__ import annotations

from typing import Optional
from urllib.parse import quote_plus

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _build_sqlalchemy_url(
    *,
    dialect: str,
    host: str,
    port: int,
    name: str,
    username: str,
    password: str,
    ssl: bool,
) -> str:
    d = dialect.lower().strip()
    if d in ("postgres", "postgresql"):
        driver = "postgresql+psycopg"
    else:
        raise ValueError(f"Unsupported DB_DIALECT={dialect!r} (use postgres)")

    user = quote_plus(username)
    pwd = quote_plus(password)
    url = f"{driver}://{user}:{pwd}@{host}:{port}/{name}"
    if ssl:
        url += "?sslmode=require"
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Payment Events Hub"

    # Preferred: non-empty DATABASE_URL (`postgresql+psycopg://…` or test `sqlite+…`).
    # If unset/blank, SQLAlchemy URL is built from DB_* (Compose / legacy split secrets).
    database_url: Optional[str] = Field(default=None, validation_alias="DATABASE_URL")

    db_dialect: str = Field(default="postgres", validation_alias="DB_DIALECT")
    db_host: str = Field(default="localhost", validation_alias="DB_HOST")
    db_port: int = Field(default=5432, validation_alias="DB_PORT")
    db_name: str = Field(default="setu", validation_alias="DB_NAME")
    db_username: str = Field(default="postgres", validation_alias="DB_USERNAME")
    db_password: str = Field(default="postgres", validation_alias="DB_PASSWORD")
    db_ssl: bool = Field(default=False, validation_alias="DB_SSL")

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_database_url(cls, value: object) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped if stripped else None
        return value

    @field_validator("db_ssl", mode="before")
    @classmethod
    def _parse_db_ssl(cls, value: object) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    @model_validator(mode="after")
    def resolve_database_url(self):
        if self.database_url and str(self.database_url).strip():
            return self
        built = _build_sqlalchemy_url(
            dialect=self.db_dialect,
            host=self.db_host,
            port=self.db_port,
            name=self.db_name,
            username=self.db_username,
            password=self.db_password,
            ssl=self.db_ssl,
        )
        object.__setattr__(self, "database_url", built)
        return self


settings = Settings()
