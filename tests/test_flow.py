from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> TestClient:
    # Import app after `tests/conftest.py` sets DATABASE_URL for deterministic sqlite tests.
    from src.app import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _evt(
    *,
    event_id: str,
    event_type: str,
    transaction_id: str,
    merchant_id: str = "merchant_1",
    merchant_name: str = "QuickMart",
    amount: float = 12.34,
    ts: str = "2026-01-01T00:00:00+00:00",
) -> dict:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "transaction_id": transaction_id,
        "merchant_id": merchant_id,
        "merchant_name": merchant_name,
        "amount": amount,
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


def test_event_idempotency_integrityerror_on_commit_is_duplicate() -> None:
    """
    Deterministic regression test for the commit-time idempotency fix.

    In real concurrency, the loser request may fail at COMMIT with a PK violation.
    We simulate that by forcing commit() to raise IntegrityError and assert the API
    returns a clean duplicate response (not a 500).
    """
    from sqlalchemy.exc import IntegrityError

    from src.app import app  # noqa: WPS433
    from src.db import get_session, SessionLocal  # noqa: WPS433

    real = SessionLocal()
    did_raise = {"v": False}

    class _Wrapped:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, item):
            return getattr(self._inner, item)

        def commit(self):
            if not did_raise["v"]:
                did_raise["v"] = True
                raise IntegrityError("duplicate", params=None, orig=Exception("pk violation"))
            return self._inner.commit()

    def _override():
        try:
            yield _Wrapped(real)
        finally:
            real.close()

    app.dependency_overrides[get_session] = _override
    try:
        with TestClient(app) as c:
            body = _evt(event_id="e-commit-race-1", event_type="payment_initiated", transaction_id="t-commit-race-1")
            r = c.post("/events", json=body)
            assert r.status_code == 200
            assert r.json()["accepted"] is False
            assert r.json()["duplicate"] is True
    finally:
        app.dependency_overrides.pop(get_session, None)


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
    ids = {row["transaction_id"] for row in d["items"]}
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


def test_reconciliation_summary_group_by_day_works_on_sqlite(client: TestClient) -> None:
    # Tests run on in-memory SQLite; ensure `group_by=day` is dialect-safe.
    txn = "t-day"
    assert client.post(
        "/events",
        json=_evt(event_id="g1", event_type="payment_initiated", transaction_id=txn, ts="2026-02-10T12:00:00+00:00"),
    ).json()["accepted"]
    assert client.post(
        "/events",
        json=_evt(event_id="g2", event_type="payment_processed", transaction_id=txn, ts="2026-02-10T12:01:00+00:00"),
    ).json()["accepted"]

    r = client.get("/reconciliation/summary", params={"group_by": "day"})
    assert r.status_code == 200
    rows = r.json()
    assert any(row.get("day") == "2026-02-10" for row in rows)


def test_duplicate_with_conflict_on_payload_mismatch(client: TestClient) -> None:
    body1 = _evt(event_id="dup-1", event_type="payment_initiated", transaction_id="t-dup-1", amount=12.34)
    assert client.post("/events", json=body1).json()["accepted"] is True

    body2 = dict(body1)
    body2["amount"] = 99.99
    r = client.post("/events", json=body2)
    assert r.status_code == 200
    payload = r.json()
    assert payload["duplicate"] is True
    assert payload.get("conflict") is True
    assert "amount" in (payload.get("conflict_fields") or [])


def test_health_includes_counts(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    j = r.json()
    assert j.get("status") == "healthy"
    assert "event_count" in j
    assert "transaction_count" in j


def test_discrepancies_includes_summary_and_pagination_shape(client: TestClient) -> None:
    r = client.get("/reconciliation/discrepancies", params={"limit": 5, "offset": 0})
    assert r.status_code == 200
    j = r.json()
    assert "items" in j
    assert "summary" in j
    assert "page" in j
    assert "total" in j["summary"]
    assert "by_type" in j["summary"]
