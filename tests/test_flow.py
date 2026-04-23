from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> TestClient:
    # Import app after `tests/conftest.py` sets DATABASE_URL for deterministic sqlite tests.
    from peh.app import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _evt(
    *,
    event_id: str,
    event_type: str,
    transaction_id: str,
    merchant_id: str = "merchant_1",
    merchant_name: str = "QuickMart",
    ts: str = "2026-01-01T00:00:00+00:00",
) -> dict:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "transaction_id": transaction_id,
        "merchant_id": merchant_id,
        "merchant_name": merchant_name,
        "amount": 12.34,
        "currency": "INR",
        "timestamp": ts,
    }


def test_event_idempotency(client: TestClient) -> None:
    body = _evt(event_id="e1", event_type="payment_initiated", transaction_id="t1")
    r1 = client.post("/events", json=body)
    assert r1.status_code == 200
    assert r1.json()["accepted"] is True

    r2 = client.post("/events", json=body)
    assert r2.status_code == 200
    assert r2.json()["accepted"] is False
    assert r2.json()["duplicate"] is True


def test_settled_after_failed_shows_in_discrepancies(client: TestClient) -> None:
    txn = "t-fail-settle"
    assert client.post("/events", json=_evt(event_id="a1", event_type="payment_initiated", transaction_id=txn)).json()[
        "accepted"
    ]
    assert client.post(
        "/events",
        json=_evt(event_id="a2", event_type="payment_failed", transaction_id=txn, ts="2026-01-01T00:01:00+00:00"),
    ).json()["accepted"]
    assert client.post(
        "/events",
        json=_evt(event_id="a3", event_type="settled", transaction_id=txn, ts="2026-01-01T00:02:00+00:00"),
    ).json()["accepted"]

    d = client.get("/reconciliation/discrepancies").json()
    ids = {row["transaction_id"] for row in d}
    assert txn in ids


def test_list_transactions_date_filter_is_sql_level(client: TestClient) -> None:
    txn = "t-date"
    assert client.post(
        "/events",
        json=_evt(event_id="d1", event_type="payment_initiated", transaction_id=txn, ts="2026-02-10T12:00:00+00:00"),
    ).json()["accepted"]

    r = client.get("/transactions", params={"from_date": "2026-02-01T00:00:00+00:00", "to_date": "2026-03-01T00:00:00+00:00"})
    assert r.status_code == 200
    ids = {row["transaction_id"] for row in r.json()["items"]}
    assert txn in ids
