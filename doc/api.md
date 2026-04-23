# API contract — Payment Events Hub

This service is implemented in **`src/`** (FastAPI). Interactive docs: **`GET /docs`** (Swagger), **`GET /redoc`**.

**Base URL:** depends on environment (e.g. `http://localhost:8000`). Paths below are relative.

**JSON:** request bodies use `Content-Type: application/json` unless noted.

---

## POST `/events`

Ingest a **single** payment lifecycle event.

**Idempotency:** Re-sending the same `event_id` with an **identical** stored payload returns **`duplicate`** with **no new row** and no lifecycle mutation. If the same `event_id` is sent again but **any compared field differs**, the response marks **`duplicate`** + **`conflict: true`** and lists differing fields in **`conflict_fields`** (no state mutation).

**409 Conflict:** If `transaction_id` already exists on a transaction owned by **another** `merchant_id`, the request fails with HTTP 409 (transaction / merchant mismatch).

### Validation & constraints

| Rule | Enforcement |
|------|-------------|
| `event_type` | One of: `payment_initiated`, `payment_processed`, `payment_failed`, `settled` |
| `amount` | Must be **> 0** |
| `currency` | Exactly **3** characters after trim; stored **uppercase** |
| `timestamp` | ISO 8601 datetime; normalized for ordering (**no** “future timestamp” rejection in the API layer) |
| ID / name lengths | Not validated by length in Pydantic; **database** columns use **64** chars for ids and **255** for `merchant_name` — oversize values can fail at persist time |

### Request

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

### Response shape (`EventIngestResponse`)

All outcomes use the **same** JSON shape; semantics come from booleans and optional fields.

**Accepted (new event stored)**

```json
{
  "accepted": true,
  "duplicate": false,
  "transaction_id": "2f86e94c-239c-4302-9874-75f28e3474ee",
  "message": null,
  "conflict": false,
  "conflict_fields": null
}
```

**Duplicate (same `event_id`, payload matches stored event)**

```json
{
  "accepted": false,
  "duplicate": true,
  "transaction_id": "2f86e94c-239c-4302-9874-75f28e3474ee",
  "message": null,
  "conflict": false,
  "conflict_fields": null
}
```

**Duplicate with conflict (same `event_id`, payload differs)**

```json
{
  "accepted": false,
  "duplicate": true,
  "transaction_id": "2f86e94c-239c-4302-9874-75f28e3474ee",
  "message": "Duplicate event_id with conflicting payload",
  "conflict": true,
  "conflict_fields": ["amount"]
}
```

**HTTP status codes**

| Code | When |
|------|------|
| `200` | Handled in application logic (`accepted` / `duplicate` / `conflict` in body) |
| `409` | `transaction_id` already tied to a different `merchant_id` |
| `422` | Validation error (see [Error responses](#error-responses)) |

> **Note:** This API uses **`accepted` / `duplicate` / `conflict`**, not a single `ingestion_status` string. Derived fields like “payment_status” / “settlement_status” are **not** echoed on this response; read them via **`GET /transactions/{id}`** after ingest.

---

## GET `/transactions`

Paged list of transactions.

### Query parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `merchant_id` | string | — | Filter by merchant |
| `status` | string | — | Matches **`payment_status`** on the transaction row (exact string, e.g. `initiated`, `processed`, `failed`) |
| `from_date` | datetime | — | Include transactions that have **≥ 1** event with `occurred_at >= from_date` |
| `to_date` | datetime | — | Same, with `occurred_at < to_date` (half-open on **event** time) |
| `limit` | int | `50` | Page size, **1–200** |
| `offset` | int | `0` | Offset |
| `sort` | string | `updated_at` | One of: `updated_at`, `created_at`, `amount`, `payment_status`, `settled_at`, `transaction_id` |
| `direction` | string | `desc` | `asc` or `desc` (OpenAPI query name; internal alias `dir`) |

> **Not implemented here:** `page` / `per_page`, `start_date` / `end_date`, or a closed list of “status” enums on this query — use **`status=`** as a free-form match to stored `payment_status`.

### Response (`TransactionListResponse`)

```json
{
  "items": [
    {
      "transaction_id": "2f86e94c-239c-4302-9874-75f28e3474ee",
      "merchant_id": "merchant_2",
      "amount": 15248.29,
      "currency": "INR",
      "payment_status": "failed",
      "has_settlement": false,
      "settled_at": null,
      "payment_conflict": false,
      "updated_at": "2026-01-08T12:38:58.085567+00:00"
    }
  ],
  "page": {
    "limit": 50,
    "offset": 0,
    "total": 3800
  }
}
```

Amounts are **numbers** in JSON (not decimal strings). Settlement is represented by **`has_settlement`** + **`settled_at`**, not a separate `settlement_status` string.

---

## GET `/transactions/{transaction_id}`

Returns one transaction (flat transaction fields), **merchant**, and **events** ordered by `occurred_at` ascending, then `event_id` ascending.

### Response (`TransactionDetailOut`)

```json
{
  "transaction_id": "2f86e94c-239c-4302-9874-75f28e3474ee",
  "merchant_id": "merchant_2",
  "amount": 15248.29,
  "currency": "INR",
  "payment_status": "failed",
  "has_settlement": false,
  "settled_at": null,
  "payment_conflict": false,
  "updated_at": "2026-01-08T12:38:58.085567+00:00",
  "merchant": {
    "merchant_id": "merchant_2",
    "merchant_name": "FreshBasket"
  },
  "events": [
    {
      "event_id": "b768e3a7-9eb3-4603-b21c-a54cc95661bc",
      "event_type": "payment_initiated",
      "transaction_id": "2f86e94c-239c-4302-9874-75f28e3474ee",
      "merchant_id": "merchant_2",
      "amount": 15248.29,
      "currency": "INR",
      "occurred_at": "2026-01-08T12:11:58.085567+00:00"
    }
  ]
}
```

| Code | When |
|------|------|
| `404` | Unknown `transaction_id` (`detail`: transaction not found) |
| `500` | Transaction exists but merchant row missing (integrity failure) |

---

## GET `/reconciliation/summary`

Aggregates over transactions that have events in the optional time window (see implementation for exact SQL).

### Query parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `group_by` | string | `merchant` | **`merchant`** \| **`day`** \| **`payment_status`** \| **`settlement`** |
| `merchant_id` | string | — | Optional filter on events |
| `from_date` | datetime | — | `occurred_at >= from_date` |
| `to_date` | datetime | — | `occurred_at < to_date` |

> **`group_by=date`** is **not** used; use **`group_by=day`**. Rows include **`txn_count`**, **`event_count`**, **`amount_sum`** per group — not `avg_amount` or per-status breakdown counts in one object (see response model in `/docs`).

### Response

JSON **array** of rows; dimensions depend on `group_by` (e.g. `merchant_id`, optional `day` as `YYYY-MM-DD`, `payment_status`, or `settlement_state` of `settled` / `unsettled`). Each row includes **`txn_count`**, **`event_count`**, **`amount_sum`**.

---

## GET `/reconciliation/discrepancies`

Lists transactions where any reconciliation flag is set, with optional **`type`** filter and pagination.

### Query parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `merchant_id` | string | — | Filter |
| `type` | string | — | Optional; must be one of the four values below |
| `limit` | int | `200` | **1–2000** |
| `offset` | int | `0` | |

### Valid `type` filter values

| `type` | Meaning (matches DB flag) |
|--------|-----------------------------|
| `payment_terminal_conflict` | Terminal payment outcome conflict |
| `processed_not_settled` | Processed but not settled |
| `settled_without_terminal_payment_outcome` | Settled without terminal processed/failed |
| `settled_after_failed` | Settled after failed terminal outcome |

Invalid `type` → **`400`** with `detail` invalid discrepancy type.

### Response (`DiscrepancyListResponse`)

```json
{
  "items": [
    {
      "transaction_id": "482ec6cc-8e86-4f4f-adb2-2f74e2bbf0da",
      "merchant_id": "merchant_3",
      "payment_status": "failed",
      "terminal_payment_status": "failed",
      "has_settlement": true,
      "settled_at": "2026-01-10T09:00:00+00:00",
      "payment_conflict": true,
      "recon_processed_not_settled": false,
      "recon_settled_without_processed": false,
      "recon_settled_after_failed": true,
      "discrepancy_types": ["settled_after_failed"]
    }
  ],
  "summary": {
    "total": 475,
    "by_type": {
      "payment_terminal_conflict": 12,
      "processed_not_settled": 380,
      "settled_without_terminal_payment_outcome": 0,
      "settled_after_failed": 95
    }
  },
  "page": {
    "limit": 200,
    "offset": 0,
    "total": 475
  }
}
```

Items do **not** embed a full **`event_timeline`** here — use **`GET /transactions/{id}`** for the ordered event list.  
**`summary.by_type`** uses a **precedence** rule so each transaction is counted once when multiple flags apply; **`summary.total`** matches the filtered list for the current query (including `type`).

---

## GET `/health`

```json
{
  "status": "healthy",
  "database": "connected",
  "event_count": 10355,
  "transaction_count": 3800
}
```

**`503`** if the database cannot be queried (`detail`: **Database unavailable**).

---

## Error responses

Uses FastAPI’s default shapes unless overridden.

### Validation (`422 Unprocessable Entity`)

```json
{
  "detail": [
    {
      "type": "greater_than",
      "loc": ["body", "amount"],
      "msg": "Input should be greater than 0",
      "input": -100.0,
      "ctx": {"gt": 0}
    }
  ]
}
```

### Not found (`404 Not Found`)

Example for missing transaction:

```json
{
  "detail": "transaction not found"
}
```

### Bad request (`400 Bad Request`)

Example: invalid `type` on `/reconciliation/discrepancies`.

### Conflict (`409 Conflict`)

Example: `POST /events` when `transaction_id` exists for another merchant — body includes a **`detail`** string.

### Service unavailable (`503 Service Unavailable`)

Example: **`GET /health`** when DB is down — **`detail`** does not expose driver/DSN text.

---

## Quick reference (implemented vs. alternate naming)

Some write-ups use names like `ingestion_status`, `settlement_status`, `page`/`per_page`, or `start_date`/`end_date`. **This codebase** uses the field and query names above; use **`GET /docs`** for the generated schema as the source of truth.
