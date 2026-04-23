from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Payment Events Hub"

    # Must be set (non-empty) — no implicit default URL. See `.env.example`.
    database_url: str = Field(
        ...,
        validation_alias="DATABASE_URL",
    )

    @field_validator("database_url", mode="after")
    @classmethod
    def _database_url_non_empty(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError(
                "DATABASE_URL must be a non-empty SQLAlchemy URL, e.g. "
                "postgresql+psycopg://user:pass@localhost:5432/dbname"
            )
        return s


settings = Settings()
