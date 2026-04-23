# Payment Events Hub (`src`)

Backend service that ingests payment lifecycle events, stores immutable event history, maintains derived transaction state, and exposes ops/reconciliation APIs.

**GitHub repository name:** `payment-events-hub` (this folder). **Python package:** `src` (`import src`, `uvicorn src.app:app`).

**This directory (`payment-events-hub/`) is the project root**—run `git init` here. The parent `solutions-engineer/` folder only holds the take-home context; `ASSIGNMENT.md` and `sample_data/sample_events.json` are also copied here so the repo is self-contained.

## Architecture

- **FastAPI** HTTP API
- **SQLAlchemy** ORM + **PostgreSQL** (recommended for deployment)
- **Idempotency** enforced with a primary key on `events.event_id`
- **Derived fields** on `transactions` updated on ingest for fast filtering (reconciliation flags, settlement timestamps, terminal payment outcome)

## Local development (Docker + Postgres)

From this directory (`payment-events-hub/`):

```bash
docker compose up --build
```

The `api` service sets **`DATABASE_URL`** for the bundled Postgres (`db`). Override it in `docker-compose.yml` or via env if you point at another database.

API: `http://localhost:8000`

**API docs (Swagger / OpenAPI):** [Swagger UI](http://localhost:8000/docs) · [ReDoc](http://localhost:8000/redoc) · [`/openapi.json`](http://localhost:8000/openapi.json). Summaries and schema descriptions are defined in `src/app.py` and route modules. Written contract: [`doc/api.md`](doc/api.md).

### Load the provided sample dataset

In another terminal (from `payment-events-hub/`):

```bash
docker compose exec api python -m src.scripts.load_sample_events /app/sample_data/sample_events.json
```

## Local development (Python venv)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

**Database config** (in **`.env`**, start from **`.env.example`**):

- **`DATABASE_URL` is required** (non-empty SQLAlchemy URL, e.g. `postgresql+psycopg://…`). The app will not use implicit defaults; copy the example and fill in your real database.

```bash
cp .env.example .env   # then set DATABASE_URL for your machine
```

### Local Postgres (pgAdmin / PostgreSQL on your machine)

1. In pgAdmin (or `psql`), **create an empty database** and user that match what you’ll put in `DATABASE_URL`.
2. Copy **`.env.example`** to **`.env`** and set **`DATABASE_URL`** to your real SQLAlchemy URL (not the template placeholders).
3. Verify connectivity and create tables:

```bash
python -m src.scripts.verify_db
```

4. Run the API:

```bash
uvicorn src.app:app --reload
```

5. Optional — load sample events (path is relative to current directory):

```bash
python -m src.scripts.load_sample_events sample_data/sample_events.json
```

### Tests (SQLite, no Postgres required)

```bash
pytest
```

Unit tests use an in-memory SQLite DB by default so they do not depend on your Postgres.

## API

### `POST /events`

Ingest a single event. Duplicate `event_id` values are rejected (`accepted=false`, `duplicate=true`) and do not mutate state.

### `GET /transactions`

Query parameters:

- `merchant_id`
- `status` (matches `transactions.payment_status`)
- `from_date`, `to_date` (filters to transactions that have **any** event in `[from_date, to_date)`)
- `limit`, `offset`
- `sort` one of: `updated_at`, `created_at`, `amount`, `payment_status`, `settled_at`, `transaction_id`
- `direction` (`asc`/`desc`)

### `GET /transactions/{transaction_id}`

Returns merchant + ordered event history.

### `GET /reconciliation/summary`

Query parameters:

- `merchant_id` (optional)
- `from_date`, `to_date` (optional; based on **event timestamps**)
- `group_by`: `merchant` | `day` | `payment_status` | `settlement`

Response rows include `txn_count`, `event_count`, and `amount_sum` (txn amounts are de-duplicated; event counts reflect filtered events).

### `GET /reconciliation/discrepancies`

Returns transactions where any of the following is true:

- `payment_conflict` (conflicting terminal payment outcomes detected with out-of-order events)
- `recon_processed_not_settled`
- `recon_settled_without_processed`
- `recon_settled_after_failed`

## Deployment notes

This repo includes a `Dockerfile` + `docker-compose.yml` suitable for platforms that can run containers. For managed Postgres, set **`DATABASE_URL`** to a full SQLAlchemy URL in the environment (e.g. `sslmode=require` in the query string if the provider needs SSL).

## Tradeoffs / assumptions

- **Current status** is derived from event timestamps with `(timestamp, event_id)` tie-breaking for deterministic ordering.
- **Settlement** uses “first settlement wins” for `settled_at` if multiple `settled` events exist.
- **Amount** on `transactions` reflects the latest ingested event payload (sample data keeps amounts consistent per transaction).
