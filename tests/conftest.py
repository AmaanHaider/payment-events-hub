import os

# Force in-memory SQLite for pytest so `.env` / Postgres never shadow test isolation.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
