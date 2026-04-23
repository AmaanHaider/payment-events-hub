from __future__ import annotations

import time
from typing import Optional

import psycopg

from peh.config import settings


def main() -> None:
    dsn = settings.database_url
    if not dsn:
        raise SystemExit("database URL could not be resolved (set DATABASE_URL or DB_* vars)")

    # psycopg expects a postgres:// or postgresql:// URI; strip optional +psycopg driver hint.
    dsn = dsn.replace("postgresql+psycopg://", "postgresql://")

    deadline = time.time() + 60
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            return
        except Exception as e:  # noqa: BLE001 - startup probe
            last_err = e
            time.sleep(0.5)

    raise SystemExit(f"Database not ready after 60s: {last_err}")


if __name__ == "__main__":
    main()
