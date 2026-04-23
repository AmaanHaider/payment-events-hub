"""ASGI application factory surface: FastAPI instance and lifespan hooks."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from peh.config import settings
from peh.db import engine
from peh.models import Base
from peh.routers import events, reconciliation, transactions


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.include_router(events.router)
app.include_router(transactions.router)
app.include_router(reconciliation.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
