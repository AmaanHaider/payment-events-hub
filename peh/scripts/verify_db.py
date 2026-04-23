"""Verify configured DB URL (DATABASE_URL or built from DB_*) — connect + create_all."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine.url import make_url

from peh.config import settings
from peh.db import engine
from peh.models import Base


def main() -> None:
    safe = make_url(settings.database_url).render_as_string(hide_password=True)
    print("Connecting with:", safe)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    print("OK: connected")
    Base.metadata.create_all(bind=engine)
    print("OK: metadata.create_all finished")


if __name__ == "__main__":
    main()
