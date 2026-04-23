"""
One-shot exercise of every public HTTP route (in-memory SQLite via conftest).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> TestClient:
    from src.app import app  # noqa: WPS433

    with TestClient(app) as c:
        yield c


def _body(*, eid: str, tid: str, event_type: str = "payment_initiated", ts: str = "2026-01-15T10:00:00+00:00") -> dict:
    return {
        "event_id": eid,
        "event_type": event_type,
        "transaction_id": tid,
        "merchant_id": "m_smoke",
        "merchant_name": "Smoke",
        "amount": 1.0,
        "currency": "USD",
        "timestamp": ts,
    }


def test_all_routes_happy_path(client: TestClient) -> None:
    tid = "t-smoke-all"
    r = client.get("/health")
    assert r.status_code == 200
    h = r.json()
    assert h.get("status") == "healthy"
    assert "event_count" in h

    r = client.post("/events", json=_body(eid="e-smoke-1", tid=tid))
    assert r.status_code == 200
    assert r.json()["accepted"] is True

    r = client.get("/transactions", params={"limit": 10, "offset": 0, "direction": "desc"})
    assert r.status_code == 200
    tdata = r.json()
    assert "items" in tdata and "page" in tdata
    assert any(x["transaction_id"] == tid for x in tdata["items"])

    r = client.get(f"/transactions/{tid}")
    assert r.status_code == 200
    d = r.json()
    assert d.get("transaction_id") == tid
    assert "merchant" in d and "events" in d

    for group_by in ("merchant", "day", "payment_status", "settlement"):
        r = client.get("/reconciliation/summary", params={"group_by": group_by})
        assert r.status_code == 200, group_by
        assert isinstance(r.json(), list)

    r = client.get(
        "/reconciliation/discrepancies",
        params={"limit": 20, "offset": 0},
    )
    assert r.status_code == 200
    disc = r.json()
    for key in ("items", "summary", "page"):
        assert key in disc
    assert "total" in disc["summary"] and "by_type" in disc["summary"]


def test_reconciliation_discrepancies_type_filter_400_on_bad_type(client: TestClient) -> None:
    r = client.get("/reconciliation/discrepancies", params={"type": "nope_not_a_type"})
    assert r.status_code == 400
