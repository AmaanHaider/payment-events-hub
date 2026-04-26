# Payment Events Hub

| | |
|---|---|
| **Live API (HTTPS)** | **`https://payment-events-hub-production.up.railway.app`** |
| **Swagger UI** | [`/docs`](https://payment-events-hub-production.up.railway.app/docs) |
| **ReDoc** | [`/redoc`](https://payment-events-hub-production.up.railway.app/redoc) |
| **OpenAPI JSON** | [`/openapi.json`](https://payment-events-hub-production.up.railway.app/openapi.json) |
| **Health** | [`/health`](https://payment-events-hub-production.up.railway.app/health) |

Deployed on **Railway** (replace the host above if you fork or redeploy under a different domain). `GET /` has no handler (expect **404**); use `/docs` or `/health` to verify the service.

---

This README documents the **Payment Events Hub** service: a FastAPI backend that ingests payment lifecycle events, stores immutable history, maintains derived fields on `transactions` for list/detail and reconciliation APIs, and exposes the routes described in this file and in OpenAPI (`/openapi.json`).

**Take-home context:** The original problem statement lives in [`assisgnment-doc/ASSIGNMENT.md`](assisgnment-doc/ASSIGNMENT.md). This README is the implementation-facing companion.

### Use of AI tools

I used **Cursor** with integrated AI while building this project (code, config, and documentation). I reviewed changes, ran tests, and manually verified behavior locally and on the deployed URL. The design decisions and final submission are mine.

---

## Table of contents

1. [Use of AI tools](#use-of-ai-tools)
2. [Project overview](#1-project-overview)
3. [Architecture and flow](#2-architecture-and-flow)
4. [Data model (high level)](#3-data-model-high-level)
5. [API reference](#4-api-reference)
6. [Live deployment](#5-live-deployment)
7. [Deployment guide (Railway)](#6-deployment-guide-railway)
8. [Local setup](#7-local-setup)
9. [How it works (deep dive)](#8-how-it-works-deep-dive)
10. [Running the project](#9-running-the-project)
11. [Scripts and utilities](#10-scripts-and-utilities)
12. [Testing](#11-testing)
13. [OpenAPI, Swagger, and ReDoc](#12-openapi-swagger-and-redoc)
14. [Project structure](#13-project-structure)
15. [Assumptions and tradeoffs](#14-assumptions-and-tradeoffs)

---

## 1. Project overview

### 1.1 Problem

Partners receive **payment lifecycle events** from multiple upstream systems. Those events can arrive out of order or more than once. The service must:

- **Ingest** events with **idempotency** (same logical event should not be stored twice, and replays must not corrupt state).
- **Preserve** a full, ordered **event history** per transaction.
- **Derive** a current view of each transaction (payment state, settlement, reconciliation flags) suitable for list/detail APIs.
- **Report** on reconciliation: aggregates by merchant/day/status/settlement, and a **discrepancy** feed for operations.

### 1.2 What this project provides

- An HTTP **REST API** (FastAPI) backed by a **SQL** database (PostgreSQL in production; SQLite for tests).
- **Schema migrations** via **Alembic** for PostgreSQL; automatic bootstrap on app startup.
- A large **sample dataset** (`sample_data/sample_events.json`) to exercise queries and edge cases: **10,355 events** across **5 merchants** (assignment asks for 10,000+ events and 3+ merchants).
- A **Postman collection** and **pytest** coverage for key flows and edge cases.
- A **containerized** deployment path (`Dockerfile` + `docker-compose.yml`).

### 1.3 Technology stack

Versions below are **minimums** from [`pyproject.toml`](pyproject.toml) (actual installed versions may be higher after `pip install`).

#### Runtime and language

| Item | Detail |
|------|--------|
| **Python** | **3.9+** (`requires-python` in `pyproject.toml`). The [`Dockerfile`](Dockerfile) uses **Python 3.12** for container images. |
| **Package layout** | Installable package `src` via **setuptools** (`pip install -e .`). |

#### Web and API

| Component | Package / role |
|-----------|----------------|
| **HTTP API** | [**FastAPI**](https://fastapi.tiangolo.com/) `>=0.115` — routing, dependency injection, automatic **OpenAPI** / **JSON Schema** for models. |
| **ASGI server** | [**Uvicorn**](https://www.uvicorn.org/) `[standard] >=0.30` — serves the app in dev, Docker, and production (bind `0.0.0.0`, port from `PORT` or `8000`). |
| **Request/response models** | [**Pydantic**](https://docs.pydantic.dev/) v2 (pulled in by FastAPI) — validation and serialization for ingest and list/detail payloads. |
| **Settings** | [**pydantic-settings**](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) `>=2.6` — loads **`DATABASE_URL`** from the environment (and optional `.env`). |

#### Data access and persistence

| Component | Package / role |
|-----------|----------------|
| **ORM** | [**SQLAlchemy**](https://www.sqlalchemy.org/) `>=2.0.30` — `Session`, models in `src/models.py`, query building in routers. |
| **PostgreSQL driver** | [**psycopg**](https://www.psycopg.org/) `[binary] >=3.2` — use SQLAlchemy URLs like `postgresql+psycopg://...`. |
| **Migrations** | [**Alembic**](https://alembic.sqlalchemy.org/) `>=1.13` — revision [`0001_init`](alembic/versions/0001_init.py); applied on app startup for PostgreSQL via `init_db()` in `src/app.py`. |
| **SQLite (tests only)** | Standard library + SQLAlchemy URL `sqlite+pysqlite:///:memory:` — forced in [`tests/conftest.py`](tests/conftest.py). **[greenlet](https://github.com/python-greenlet/greenlet)** `>=3.0` supports async-adjacent ORM usage with SQLite. |

#### Operations and quality

| Component | Package / role |
|-----------|----------------|
| **Containers** | [**Docker**](https://www.docker.com/) — [`Dockerfile`](Dockerfile) for production-style runs; [`docker-compose.yml`](docker-compose.yml) runs **API + PostgreSQL 16** locally. |
| **DB startup probe (compose)** | `src/scripts/wait_for_db.py` uses **psycopg** to wait for Postgres before Uvicorn starts. |
| **Tests** | [**pytest**](https://pytest.org/) `>=8.3`; [**httpx**](https://www.python-httpx.org/) `>=0.27` for calling the test app. Declared under `[project.optional-dependencies] dev` — install with `pip install -e ".[dev]"`. |

#### Databases by environment

| Environment | Database |
|-------------|----------|
| **Production** (e.g. Railway) | **PostgreSQL** — set `DATABASE_URL` to a `postgresql+psycopg://` (or compatible) URL. |
| **Local (Docker Compose)** | **PostgreSQL 16** in the `db` service; `DATABASE_URL` points at that container. |
| **Unit tests** | **SQLite** in-memory only — no Postgres required. |

---

## 2. Architecture and flow

### 2.1 Logical architecture

Request handlers do not rely on in-memory state between calls; the **database** is the source of truth. Running **multiple** API processes against the same `DATABASE_URL` is possible in principle, but you must size **connection pools** and the database for the combined load.

```mermaid
flowchart TB
  subgraph clients["Clients / ops tools / Postman"]
    C1[Ingestion client]
    C2[Dashboard / scripts]
  end

  subgraph api["API layer (FastAPI)"]
    R1[Route handlers: events, transactions, reconciliation]
    R2[Health]
    D[Dependency: DB session per request]
  end

  subgraph services["Domain services"]
    S[Ingestion: idempotency + derived state]
  end

  subgraph data["Data layer"]
    DB[(PostgreSQL / SQLite)]
  end

  C1 --> R1
  C2 --> R1
  C2 --> R2
  R1 --> D
  R2 --> D
  D --> S
  S --> DB
  R1 --> DB
```

### 2.2 Request lifecycle (ingest)

1. **HTTP `POST /events`** body is validated to `EventIn` (Pydantic).
2. **`ingest_payment_event()`** (see `src/services/ingestion.py`):
   - If `event_id` **already exists** → duplicate handling (and optional **conflict** if payload differs).
   - Otherwise → upsert `merchants`, create/update `transactions`, `INSERT` into `events` (immutable), then apply **payment lifecycle** rules to update derived columns on `transactions`, then refresh **reconciliation flags**.
3. The route **commits** on success or **rolls back** for duplicate paths.

### 2.3 Request lifecycle (read APIs)

- **List/detail** and **reconciliation** endpoints build **SQLAlchemy** queries (including aggregates and `GROUP BY` in the database) and return Pydantic response models.

### 2.4 Startup: database initialization

On application startup (`src/app.py` **lifespan**), `init_db()` runs:

- **SQLite (tests):** `Base.metadata.create_all()`.
- **PostgreSQL:** runs **Alembic** `upgrade head` using `alembic.ini` at the repo root. If tables already exist from an older `create_all` but `alembic_version` is missing, the app **stamps** revision `0001_init` then upgrades—this avoids `CREATE` collisions.

---

## 3. Data model (high level)

### 3.1 Tables

| Table | Role |
|--------|------|
| `merchants` | One row per merchant; `merchant_id` PK. Name can be updated on each ingest. |
| `transactions` | One row per `transaction_id` PK. Holds **denormalized** amount/currency and **derived** payment/settlement and reconciliation **flags** updated on every accepted event. |
| `events` | **Immutable** history: one row per `event_id` PK, FKs to `transactions` and `merchants`. Optional `raw_json` snapshot. |

**Idempotency:** `events.event_id` is the **primary key**—duplicate inserts are impossible if the first insert committed.

### 3.2 Important derived fields on `transactions`

- **Payment status fields:** `payment_status` (last relevant non-settlement lifecycle), `last_payment_event_*`, `terminal_payment_status` and timestamps (for processed vs failed), `payment_conflict`.
- **Settlement:** `has_settlement`, `settled_at`, `settlement_event_id`. The first `settled` event turns settlement on. If a later ingested `settled` event has a **strictly smaller** `(occurred_at, event_id)` than the current stored settlement, the row is updated to that event (out-of-order or replay scenarios). See `apply_payment_lifecycle()` for `EventType.SETTLED` in `src/services/ingestion.py`.
- **Reconciliation flags (precomputed for fast filters):** `recon_processed_not_settled`, `recon_settled_without_processed`, `recon_settled_after_failed`.

### 3.3 Indexes

See `alembic/versions/0001_init.py` and `src/models.py` for `Index(...)` on merchant, status, time ranges, and event timelines.

---

## 4. API reference

**Base path:** all routes in this project are defined **without** a global prefix (e.g. `POST /events`, not `/api/v1/events`).

**Interactive docs (any running instance):**

- **Swagger UI:** `GET /docs`
- **ReDoc:** `GET /redoc`
- **OpenAPI JSON:** `GET /openapi.json`

> **Note:** `GET /` is **not** defined. The service returns **404** for the site root. Use `/docs` or `/health` for a quick sanity check.

---

### 4.1 `GET /health`

| | |
|---|---|
| **Purpose** | Liveness and database connectivity. Runs `SELECT 1` and returns coarse row counts. |
| **Query parameters** | None |
| **Success (200)** | JSON: `status`, `database`, `event_count`, `transaction_count` |
| **Failure (503)** | `{"detail": "Database unavailable"}` if DB is unreachable or queries fail. |

**Example:**

```http
GET /health
```

**Example response shape (200)** — `event_count` and `transaction_count` match **your** database, not the numbers below:

```json
{
  "status": "healthy",
  "database": "connected",
  "event_count": 0,
  "transaction_count": 0
}
```

**Typical use:** health checks for load balancers, quick verification after deploy, monitoring.

---

### 4.2 `POST /events`

| | |
|---|---|
| **Purpose** | Ingest a single payment lifecycle event. **Idempotent** on `event_id`. |
| **Content-Type** | `application/json` |
| **Success path** | **HTTP 200** with body describing accepted vs duplicate (see below). `409` is reserved for a specific merchant/transaction rule. |

**Request body (`EventIn`):**

| Field | Type | Notes |
|--------|------|--------|
| `event_id` | string | **Idempotency key** (primary key in DB). |
| `event_type` | enum | `payment_initiated` · `payment_processed` · `payment_failed` · `settled` |
| `transaction_id` | string | Groups events for one payment. |
| `merchant_id` | string | Foreign key; merchant row is upserted. |
| `merchant_name` | string | Stored/updated on merchant. |
| `amount` | number | Must be `> 0`. |
| `currency` | string | 3 letters; normalized to **uppercase**. |
| `timestamp` | string (ISO 8601) | Event time; used for ordering and lifecycle. |

**Response (`EventIngestResponse`):**

| Field | Meaning |
|--------|--------|
| `accepted` | `true` only if a new event row was persisted. |
| `duplicate` | `true` if `event_id` was already present. |
| `conflict` | `true` if same `event_id` but **different** payload than stored. |
| `conflict_fields` | Which fields differed (e.g. `["amount", "timestamp"]`). |
| `message` | Human-readable reason for non-accept paths. |
| `transaction_id` | Echo. |

**Example (new event):**

```json
{
  "event_id": "b768e3a7-9eb3-4603-b21c-a54cc95661bc",
  "event_type": "payment_initiated",
  "transaction_id": "2f86e94c-239c-4302-9874-75f28e3474ee",
  "merchant_id": "merchant_2",
  "merchant_name": "FreshBasket",
  "amount": 15248.29,
  "currency": "INR",
  "timestamp": "2026-01-08T12:11:58.085567+00:00"
}
```

**HTTP 409** occurs when a client tries to attach an existing `transaction_id` to a **different** `merchant_id` than stored.

**Typical use:** partner webhooks, batch loaders, and replay/safety (duplicates are safe).

---

### 4.3 `GET /transactions`

| | |
|---|---|
| **Purpose** | Paged list of transactions with optional filters. |

**Query parameters:**

| Name | Type | Description |
|------|------|-------------|
| `merchant_id` | string, optional | Exact match on `transactions.merchant_id`. |
| `status` | string, optional | Exact match on **derived** `transactions.payment_status`. |
| `from_date` | datetime, optional | Include transactions with **at least one event** where `occurred_at >= from_date`. |
| `to_date` | datetime, optional | Include transactions with **at least one event** where `occurred_at < to_date` (half-open window with `from_date`). |
| `limit` | int, default `50`, max `200` | Page size. |
| `offset` | int, default `0` | Skip rows. |
| `sort` | enum, default `updated_at` | `updated_at` · `created_at` · `amount` · `payment_status` · `settled_at` · `transaction_id` |
| `direction` | enum, default `desc` | `asc` or `desc` |

**Response (`TransactionListResponse`):**

- `items`: list of `TransactionOut`
- `page`: `limit`, `offset`, `total` (total matching rows for the filter)

**Example:**

```http
GET /transactions?merchant_id=merchant_1&status=processed&from_date=2026-01-01T00:00:00%2B00:00&to_date=2027-01-01T00:00:00%2B00:00&limit=20&offset=0&sort=updated_at&direction=desc
```

**Typical use:** ops console lists, support search by merchant, monitoring queues.

---

### 4.4 `GET /transactions/{transaction_id}`

| | |
|---|---|
| **Purpose** | One transaction: **derived fields**, **merchant** snapshot, full **event history** in deterministic order. |
| **404** | Unknown `transaction_id`. |
| **500** | Should not happen in normal operation (transaction without merchant). |

**Ordering of events:** `occurred_at` ascending, then `event_id` ascending.

**Typical use:** support investigation, chargeback and lifecycle tracing.

---

### 4.5 `GET /reconciliation/summary`

| | |
|---|---|
| **Purpose** | Aggregated **transaction and event** metrics grouped by a chosen dimension, within an optional time/merchant event window. |

**Query parameters:**

| Name | Description |
|------|-------------|
| `group_by` | `merchant` (default) · `day` · `payment_status` · `settlement` |
| `merchant_id` | Optional: restrict to events for this merchant. |
| `from_date` / `to_date` | Optional: filter **events** by `occurred_at` in `[from, to)` |

**Semantics:**

- The **window** is defined by events: only transactions with **≥1** matching event are in scope.
- **`txn_count`** and **`amount_sum`** deduplicate by transaction; **`event_count`** counts **matching** events (can be >1 per transaction).

**Response:** JSON array of `ReconciliationSummaryRow` (fields depend on `group_by`; e.g. `settlement` uses `settlement_state` of `settled` vs `unsettled`).

**Example:**

```http
GET /reconciliation/summary?group_by=merchant&from_date=2026-01-01T00:00:00%2B00:00&to_date=2027-01-01T00:00:00%2B00:00
```

**Typical use:** daily ops dashboards, finance reporting by day or merchant.

---

### 4.6 `GET /reconciliation/discrepancies`

| | |
|---|---|
| **Purpose** | List transactions with **reconciliation** issues, with optional type filter and pagination. |

**Query parameters:**

| Name | Default | Description |
|------|---------|-------------|
| `merchant_id` | — | Optional filter. |
| `type` | — | One of: `payment_terminal_conflict` · `processed_not_settled` · `settled_without_terminal_payment_outcome` · `settled_after_failed` |
| `limit` | `200` | Max `2000` |
| `offset` | `0` | |

**Response (`DiscrepancyListResponse`):**

- `items[]`: per-transaction flags + `discrepancy_types` (human-readable list derived from flags)
- `summary.total`: count matching current filters
- `summary.by_type`: counts with **precedence** so a row is not double-counted across types (see `case(...)` in code)
- `page`: `limit`, `offset`, `total`

**Example:**

```http
GET /reconciliation/discrepancies?type=settled_after_failed&limit=50&offset=0
```

**Typical use:** recon queues, exception handling, partner escalation.

---

## 5. Live deployment

The **live base URL and quick links** are also in the [table at the top of this README](#payment-events-hub).

If you deploy to Railway (or any host), your **base URL** is whatever the platform assigns (custom domain or default `*.up.railway.app`). This repository does not guarantee any third-party host is up: **verify** with a browser or `curl`.

**One deployment used while building this project** (example only; it may be torn down or replaced):

| Resource | URL (example host) |
|----------|-----|
| **API base** | `https://payment-events-hub-production.up.railway.app` |
| **Swagger UI** | `https://payment-events-hub-production.up.railway.app/docs` |
| **ReDoc** | `https://payment-events-hub-production.up.railway.app/redoc` |
| **OpenAPI** | `https://payment-events-hub-production.up.railway.app/openapi.json` |
| **Health** | `https://payment-events-hub-production.up.railway.app/health` |

A correct deployment serves the same routes as a local run. **`GET /`** is not defined (typically **404**). Prefer **`/docs`**, **`/redoc`**, or **`/health`**.

> Railway **`502` / "Application failed to respond`** usually means the process never bound to `$PORT`, crashed on boot, or exited; check the service **logs** and `DATABASE_URL` (see [§6](#6-deployment-guide-railway)).

---

## 6. Deployment guide (Railway)

These steps match a typical **GitHub → Railway** flow. Exact UI labels can change; the intent is fixed.

### 6.1 Prerequisites

- Source in a **Git** repository (GitHub/GitLab).
- A **container** that starts Uvicorn on the platform port (this repo’s `Dockerfile` does that).

### 6.2 Create the project and database

1. In [Railway](https://railway.app), create a **New Project**.
2. Add **PostgreSQL** (Railway “Plugin”/database service).
3. Note the **connection URL** (internal or public, depending on how you connect services on Railway).

### 6.3 Add the application service

1. **New Service** → **Deploy from GitHub** (or equivalent) and select this repository.
2. Railway should detect the **`Dockerfile`** at the repository root.  
3. In the service **Settings → Variables**:
   - Set **`DATABASE_URL`** to the Postgres URL. Prefer **Railway’s “variable reference”** from the Postgres service (e.g. `${{Postgres.DATABASE_URL}}` style, per Railway’s current UI) so credentials rotate with the database.
4. **Generate a public domain** under **Settings → Networking / Domains** (or equivalent) so the API has an HTTPS URL.

### 6.4 Port and health

- The container must listen on **`0.0.0.0`** and **`PORT`** (Railway injects `PORT`). This repo’s default command:  
  `uvicorn src.app:app --host 0.0.0.0 --port ${PORT:-8000}`.
- You can use **`GET /health`** as a manual or automated probe.

### 6.5 First deploy issues checklist

| Symptom | Likely cause |
|--------|----------------|
| `502` / “Application failed to respond” | Process not listening on `PORT` or crash on boot (check logs; verify `DATABASE_URL`). |
| Migration errors | Ensure `alembic.ini` and `alembic/` are in the image (this repo’s Dockerfile copies them). |
| DB connection refused | `DATABASE_URL` wrong service reference or wrong network on Railway. |

### 6.6 Load sample data (optional, on deploy)

If you have shell access to a one-off run (or run locally against the same DB), use the loader in **§10.2** under [Scripts and utilities](#10-scripts-and-utilities) (`load_sample_events.py`).

---

## 7. Local setup

### 7.1 Requirements

- **Python 3.9+** (see `pyproject.toml`; Docker uses 3.12)
- **PostgreSQL** (optional if you use Docker Compose for DB)
- **Git**

### 7.2 Environment variables

| Variable | Required | Purpose |
|----------|----------|--------|
| `DATABASE_URL` | **Yes** (non-empty) | SQLAlchemy URL, e.g. `postgresql+psycopg://user:pass@host:5432/dbname` |

**Setup:**

```bash
cp .env.example .env
# Edit .env: set a real DATABASE_URL
```

`src/config.py` **rejects** an empty `DATABASE_URL` (no hidden default to localhost).

**Tests:** `tests/conftest.py` forces `DATABASE_URL=sqlite+pysqlite:///:memory:` so local `.env` does not break pytest.

### 7.3 Python dependencies (editable install)

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

- Production/runtime deps: `pyproject.toml` `[project] dependencies`
- Dev (pytest, httpx): `pip install -e ".[dev]"`

### 7.4 Database setup (PostgreSQL, local)

1. Create an empty database and user matching your `DATABASE_URL`.
2. You do **not** need to run Alembic manually for normal app runs: **starting the app** runs migrations via `init_db()`.
3. Optional connectivity check: `python -m src.scripts.verify_db` (see **§10.3** under [Scripts and utilities](#10-scripts-and-utilities) — note it uses `create_all`, mainly for a quick local smoke test).

### 7.5 Sample data (optional)

File: `sample_data/sample_events.json` — **10,355** events, **5** merchants. Load after the API/DB is up (see **§10.2** under [Scripts and utilities](#10-scripts-and-utilities)).

---

## 8. How it works (deep dive)

### 8.1 Module map

| Module | Responsibility |
|--------|------------------|
| `src/app.py` | FastAPI app, `lifespan` → `init_db()`, `GET /health`, router includes. |
| `src/config.py` | `Settings` from env (`DATABASE_URL`). |
| `src/db.py` | SQLAlchemy `engine` and `SessionLocal`; SQLite uses `StaticPool` for tests. |
| `src/deps.py` | `DbSession` type alias for `Depends(get_session)`. |
| `src/models.py` | ORM models: `Merchant`, `Transaction`, `Event`. |
| `src/domain.py` | `EventType`, `PaymentStatus` enums and terminal-payment set. |
| `src/schemas.py` | Pydantic request/response models. |
| `src/services/ingestion.py` | **Core domain logic:** idempotency, lifecycle, conflicts, row locking on Postgres, reconciliation refresh. |
| `src/routers/*.py` | HTTP layer only (thin). |
| `alembic/` | Revision `0001_init` creates tables and indexes. |

### 8.2 Idempotency and concurrency

- **Primary idempotency:** `events.event_id` PK. Duplicate `event_id` with identical payload: **no-op duplicate**.
- **Conflicting replay:** same `event_id` with different canonical fields returns **`duplicate: true`**, **`conflict: true`**, and `conflict_fields`.
- **Races:** route handler catches `IntegrityError` on commit for concurrent double-inserts of the same `event_id` (rare) and returns duplicate.
- **Postgres per-transaction serialization:** on Postgres, after initial flushes, the code can **`SELECT ... FOR UPDATE`** the transaction row to reduce race conditions for concurrent events on the same `transaction_id`.

### 8.3 Payment lifecycle rules (simplified)

Implemented in `apply_payment_lifecycle()`:

- **Non-terminal events** (`payment_initiated`) can update “latest” metadata when newer by `(timestamp, event_id)`.
- **Terminal** events (`payment_processed` / `payment_failed`) update `terminal_*` and may set `payment_conflict` if two different terminal outcomes compete (with ordering rules).
- **Settlement** (`settled`) updates `has_settlement`, `settled_at`, and `settlement_event_id` according to the rules in `apply_payment_lifecycle()` (including later replays with an earlier `(occurred_at, event_id)` key).

### 8.4 Reconciliation flags

After each accepted ingest, `refresh_reconciliation_flags()` sets:

- `recon_processed_not_settled`
- `recon_settled_without_processed`
- `recon_settled_after_failed`

These power **`GET /reconciliation/discrepancies`** without scanning full history on each request.

### 8.5 Optional full replay

`src/scripts/recompute_flags.py` can rebuild all derived state from `events` (useful after bulk load or if you need to verify invariants). See **§10.4** under [Scripts and utilities](#10-scripts-and-utilities).

---

## 9. Running the project

### 9.1 Uvicorn (local venv, Postgres)

With `.env` configured:

```bash
source .venv/bin/activate
uvicorn src.app:app --host 0.0.0.0 --port 8000 --reload
```

- API: `http://localhost:8000`
- Docs: `http://localhost:8000/docs`

### 9.2 Docker Compose (API + PostgreSQL)

From the repo root:

```bash
docker compose up --build
```

- The `api` service sets `DATABASE_URL` to the bundled `db` service.
- Command waits for DB (`wait_for_db`) then runs Uvicorn on port **8000** inside the container, mapped to host **8000**.

### 9.3 Production-like container (single process)

The `Dockerfile` **CMD** uses `$PORT` for PaaS compatibility. For a local raw Docker run, pass `-e PORT=8000` and `-e DATABASE_URL=...` as needed.

---

## 10. Scripts and utilities

### 10.1 `src/scripts/wait_for_db.py`

- **Purpose:** Block until PostgreSQL accepts connections (up to 60s). Strips the `+psycopg` driver hint for `psycopg`’s DSN.
- **Used by:** `docker-compose.yml` before Uvicorn starts.

**Run manually:**

```bash
python -m src.scripts.wait_for_db
```

### 10.2 `src/scripts/load_sample_events.py`

- **Purpose:** Bulk load `sample_data/sample_events.json` (JSON **array** of event objects) through the same `ingest_payment_event` logic as the API.
- **Arguments:** `path` (optional, default `sample_data/sample_events.json`), `--commit-every N` (default `500`).

**Run:**

```bash
python -m src.scripts.load_sample_events sample_data/sample_events.json
```

**With Docker Compose:**

```bash
docker compose exec api python -m src.scripts.load_sample_events /app/sample_data/sample_events.json
```

### 10.3 `src/scripts/verify_db.py`

- **Purpose:** Print masked `DATABASE_URL`, run `SELECT 1`, then `Base.metadata.create_all()`.
- **Note:** The **main application** on PostgreSQL prefers **Alembic** via `init_db()`. This script is a quick “can I connect and create tables?” aid; for a clean production-like DB, use migrations.

**Run:**

```bash
python -m src.scripts.verify_db
```

### 10.4 `src/scripts/recompute_flags.py`

- **Purpose:** For every distinct `transaction_id` in `events`, replay events in order and recompute derived columns and flags. Optional `--commit-every` (default `250`).

**Run:**

```bash
python -m src.scripts.recompute_flags
```

---

## 11. Testing

### 11.1 Pytest

```bash
pytest
```

- Tests use **in-memory SQLite** (see `tests/conftest.py`).
- Representative modules:
  - `tests/test_all_api_routes_smoke.py` — route smoke tests
  - `tests/test_flow.py` — end-to-end flow
  - `tests/test_edge_cases.py` — idempotency, conflicts, etc.
  - `tests/test_full_dataset_optin.py` — optional heavier tests (gated; read file for env flags)

### 11.2 Postman

- **Collection file:** `postman/payment-events-hub.postman_collection.json`
- **Variable:** `baseUrl` (default `http://localhost:8000`). For a cloud deployment, set `baseUrl` to **your** deployed origin (no trailing slash; requests use `{{baseUrl}}/...`).

**Import:** Postman → **Import** → select the JSON file.

**Requests included:** `GET /health`, `POST /events` (sample + duplicate conflict), `GET /transactions`, `GET /transactions/{id}`, `GET /reconciliation/summary`, `GET /reconciliation/discrepancies`.

### 11.3 Manual endpoint checklist (curl)

Replace `BASE` with your environment.

```bash
BASE="http://localhost:8000"   # or your deployed base URL, e.g. https://<your-app>.up.railway.app

curl -sS "$BASE/health" | jq .
curl -sS "$BASE/transactions?limit=5" | jq .
curl -sS "$BASE/reconciliation/summary?group_by=merchant" | jq .
```

---

## 12. OpenAPI, Swagger, and ReDoc

| Resource | URL |
|----------|-----|
| **Swagger UI** | `/docs` (try-it-out, schemas, examples) |
| **ReDoc** | `/redoc` |
| **OpenAPI JSON** | `/openapi.json` (the `openapi` field in the document states the spec version) |

**Tip:** The OpenAPI document is the **authoritative** list of request/response models for each route in this build.

**Additional written contract:** [`doc/api.md`](doc/api.md) (narrative API notes; OpenAPI remains source of truth for field-level schemas).

---

## 13. Project structure

```text
payment-events-hub/
├── alembic/                 # SQL migrations (env.py, versions/)
├── alembic.ini              # Alembic config (working dir for revision paths)
├── assisgnment-doc/
│   └── ASSIGNMENT.md        # Original take-home brief
├── doc/
│   └── api.md               # Written API notes (complements OpenAPI)
├── postman/
│   └── payment-events-hub.postman_collection.json
├── sample_data/
│   └── sample_events.json   # 10,355+ events, 5 merchants
├── src/
│   ├── app.py               # FastAPI app + lifespan + /health
│   ├── config.py            # Pydantic settings (DATABASE_URL)
│   ├── db.py                # Engine + session
│   ├── deps.py              # DB session dependency
│   ├── domain.py            # Enums
│   ├── models.py            # ORM models + indexes
│   ├── schemas.py           # Pydantic models
│   ├── routers/             # events, transactions, reconciliation
│   ├── services/
│   │   └── ingestion.py     # Idempotency + lifecycle + flags
│   └── scripts/             # wait_for_db, load_sample_events, verify_db, recompute_flags
├── tests/                   # pytest
├── docker-compose.yml       # API + local Postgres
├── Dockerfile               # PaaS-friendly image (Uvicorn + $PORT, Alembic files)
├── pyproject.toml           # Project metadata, dependencies, tool config
└── README.md                # This file
```

---

## 14. Assumptions and tradeoffs

- **Deterministic ordering** uses event timestamps with `(timestamp, event_id)` tie-breaking so “latest” and conflict detection are stable under reorder.
- **Derived state** (transaction row + flags) is updated on ingest for **fast** list/discrepancy queries; a full recompute is available via `recompute_flags.py` if you need to validate invariants.
- **Settlement semantics** are implemented in `apply_payment_lifecycle()` for `EventType.SETTLED` in `src/services/ingestion.py` (not re-derived here, so the code stays the source of truth).
- **No authentication** in this repository: the HTTP API is open to anyone who can reach it unless you put something in front of it.
