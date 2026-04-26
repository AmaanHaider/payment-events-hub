"""ASGI application factory surface: FastAPI instance and lifespan hooks."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from sqlalchemy import func, select, text

from src.config import settings
from src.db import engine
from src.db import SessionLocal
from src.models import Base, Event, Transaction
from src.routers import events, reconciliation, transactions

_ALEMBIC_HEAD_REVISION = "0001_init"

OPENAPI_TAGS = [
    {
        "name": "events",
        "description": "Ingest payment lifecycle events. Idempotent on `event_id`; conflicting replays return `conflict` in the body.",
    },
    {
        "name": "transactions",
        "description": "List and fetch transactions with optional filters; detail includes merchant and ordered event timeline.",
    },
    {
        "name": "reconciliation",
        "description": "Aggregated summaries (`group_by`) and paginated discrepancy rows with optional `type` filter.",
    },
    {
        "name": "health",
        "description": "Process and database connectivity probe with coarse row counts.",
    },
]


def _pg_core_schema_present() -> bool:
    with engine.connect() as conn:
        merchants = conn.execute(
            text("SELECT to_regclass('public.merchants')"),
        ).scalar()
        transactions = conn.execute(
            text("SELECT to_regclass('public.transactions')"),
        ).scalar()
        events_tbl = conn.execute(
            text("SELECT to_regclass('public.events')"),
        ).scalar()
    return bool(merchants and transactions and events_tbl)


def _alembic_version_exists() -> bool:
    with engine.connect() as conn:
        rel = conn.execute(
            text("SELECT to_regclass('public.alembic_version')"),
        ).scalar()
    return bool(rel)


def init_db() -> None:
    """
    Initialize database schema.

    - For SQLite (tests/local quickstart), use create_all() for zero-config setup.
    - For non-SQLite (e.g. Postgres), prefer Alembic migrations if available.
    """
    dialect = getattr(getattr(engine, "dialect", None), "name", "")
    if dialect == "sqlite":
        Base.metadata.create_all(bind=engine)
        return

    try:
        from alembic import command  # noqa: WPS433
        from alembic.config import Config  # noqa: WPS433
    except Exception:
        Base.metadata.create_all(bind=engine)
        return

    cfg = Config("alembic.ini")
    # If the DB was bootstrapped with create_all (e.g. before Alembic), tables exist
    # but alembic_version is missing; upgrade head would try to CREATE again and fail.
    if _pg_core_schema_present() and not _alembic_version_exists():
        command.stamp(cfg, _ALEMBIC_HEAD_REVISION)
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Payment event ingestion, derived transaction state, and reconciliation APIs. "
        "Use **Swagger UI** at `/docs`, **ReDoc** at `/redoc`, or download **`/openapi.json`**."
    ),
    openapi_tags=OPENAPI_TAGS,
    lifespan=lifespan,
)

app.include_router(events.router)
app.include_router(transactions.router)
app.include_router(reconciliation.router)

@app.get(
    "/",
    tags=["health"],
    summary="Service landing",
    description="Quick landing response with helpful links (docs, health).",
)
def root() -> dict[str, object]:
    return {
        "service": settings.app_name,
        "message": "Hello from Payment Events Hub 👋",
        "author": "Amaan Haider",
        "links": {
            "docs": "/docs",
            "redoc": "/redoc",
            "openapi": "/openapi.json",
            "health": "/health",
        },
    }


@app.get(
    "/health",
    tags=["health"],
    summary="Health check",
    description="Runs `SELECT 1` and returns row counts for `events` and `transactions`.",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Database unreachable or query failed"},
    },
)
def health() -> dict[str, object]:
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        event_count = int(db.scalar(select(func.count(Event.event_id))) or 0)
        transaction_count = int(db.scalar(select(func.count(Transaction.transaction_id))) or 0)
        return {
            "status": "healthy",
            "database": "connected",
            "event_count": event_count,
            "transaction_count": transaction_count,
        }
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable") from exc
    finally:
        db.close()
