# Payment Events Hub (`peh`)

Backend service that ingests payment lifecycle events, stores immutable event history, maintains derived transaction state, and exposes ops/reconciliation APIs.

**GitHub repository name:** `payment-events-hub` (this folder). **Python package:** `peh` (`import peh`, `uvicorn peh.app:app`).

**This directory (`payment-events-hub/`) is the project root**—run `git init` here. The parent `solutions-engineer/` folder only holds the take-home context; `ASSIGNMENT.md` and `sample_events.json` are also copied here so the repo is self-contained.

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

OpenAPI: `http://localhost:8000/docs`

### Load the provided sample dataset

In another terminal (from `payment-events-hub/`):

```bash
docker compose exec api python -m peh.scripts.load_sample_events /app/sample_events.json
```

## Local development (Python venv)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

**Database config** (in **`.env`**, see **`.env.example`**):

- **Recommended:** set **`DATABASE_URL`** to a full SQLAlchemy URL (`postgresql+psycopg://…`).
- **Alternative:** omit `DATABASE_URL` and set **`DB_*`** split variables (defaults match typical local Postgres; see `peh/config.py`).

```bash
cp .env.example .env   # then adjust for your machine
```

### Local Postgres (pgAdmin / PostgreSQL on your machine)

1. In pgAdmin (or `psql`), **create an empty database** (e.g. **`setu`**) and the user you use in `.env`.
2. Put the matching URL in **`DATABASE_URL`** in `.env` (or use split **`DB_*`** vars if you prefer).
3. Verify connectivity and create tables:

```bash
python -m peh.scripts.verify_db
```

4. Run the API:

```bash
uvicorn peh.app:app --reload
```

5. Optional — load sample events (path is relative to current directory):

```bash
python -m peh.scripts.load_sample_events sample_events.json
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

This repo includes a `Dockerfile` + `docker-compose.yml` suitable for platforms that can run containers. For managed Postgres, set either **`DATABASE_URL`** (full URL) or the same **`DB_*`** keys as secrets (e.g. `DB_HOST`, `DB_NAME`, `DB_SSL=true` if the provider requires SSL).

## Tradeoffs / assumptions

- **Current status** is derived from event timestamps with `(timestamp, event_id)` tie-breaking for deterministic ordering.
- **Settlement** uses “first settlement wins” for `settled_at` if multiple `settled` events exist.
- **Amount** on `transactions` reflects the latest ingested event payload (sample data keeps amounts consistent per transaction).
