"""
Extra edge-case coverage: HTTP status codes, conflict fields, validation, filters.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> TestClient:
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
    currency: str = "INR",
    ts: str = "2026-01-01T00:00:00+00:00",
) -> dict:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "transaction_id": transaction_id,
        "merchant_id": merchant_id,
        "merchant_name": merchant_name,
        "amount": amount,
        "currency": currency,
        "timestamp": ts,
    }


def test_same_transaction_different_merchant_returns_409(client: TestClient) -> None:
    tid = "t-409-merchant"
    assert (
        client.post(
            "/events",
            json=_evt(event_id="e-409a", event_type="payment_initiated", transaction_id=tid, merchant_id="m_a"),
        ).json()["accepted"]
    )
    r = client.post(
        "/events",
        json=_evt(event_id="e-409b", event_type="payment_initiated", transaction_id=tid, merchant_id="m_b"),
    )
    assert r.status_code == 409
    assert "merchant" in r.json()["detail"].lower()


def test_get_transaction_not_found_404(client: TestClient) -> None:
    r = client.get("/transactions/00000000-0000-0000-0000-00000000dead")
    assert r.status_code == 404


@pytest.mark.parametrize(
    "field_name,patch",
    [
        ("event_type", {"event_type": "payment_processed"}),
        ("transaction_id", {"transaction_id": "t-other-409"}),
        ("merchant_id", {"merchant_id": "merchant_b"}),
        ("currency", {"currency": "USD"}),
        ("timestamp", {"timestamp": "2026-02-01T00:00:00+00:00"}),
    ],
)
def test_duplicate_event_id_payload_conflict_per_field(
    client: TestClient, field_name: str, patch: dict
) -> None:
    eid = f"edge-dup-{field_name}"
    base = _evt(
        event_id=eid,
        event_type="payment_initiated",
        transaction_id=f"txn-dup-{field_name}",
    )
    assert client.post("/events", json=base).json()["accepted"] is True
    second = {**base, **patch, "event_id": eid}
    r = client.post("/events", json=second)
    assert r.status_code == 200
    p = r.json()
    assert p["duplicate"] is True
    assert p.get("conflict") is True
    assert field_name in (p.get("conflict_fields") or [])


def test_post_events_validation_422(client: TestClient) -> None:
    assert client.post("/events", json={}).status_code == 422
    assert (
        client.post(
            "/events",
            json=_evt(
                event_id="v1",
                event_type="payment_initiated",
                transaction_id="t-v1",
                amount=0.0,
            ),
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/events",
            json={
                "event_id": "v2",
                "event_type": "not_a_real_type",
                "transaction_id": "t-v2",
                "merchant_id": "m1",
                "merchant_name": "M",
                "amount": 1.0,
                "currency": "INR",
                "timestamp": "2026-01-01T00:00:00+00:00",
            },
        ).status_code
        == 422
    )


def test_reconciliation_discrepancies_all_valid_type_filters_200(client: TestClient) -> None:
    for t in (
        "payment_terminal_conflict",
        "processed_not_settled",
        "settled_without_terminal_payment_outcome",
        "settled_after_failed",
    ):
        r = client.get("/reconciliation/discrepancies", params={"type": t, "limit": 5})
        assert r.status_code == 200, t
        assert "items" in r.json()


def test_transactions_pagination_high_offset_empty_page(client: TestClient) -> None:
    r = client.get("/transactions", params={"offset": 9_999_999, "limit": 10})
    assert r.status_code == 200
    j = r.json()
    assert j["items"] == []
    assert "page" in j
    assert j["page"]["offset"] == 9_999_999


def test_payment_terminal_conflict_listed_under_discrepancy_filter(client: TestClient) -> None:
    tid = "t-term-conflict"
    assert client.post("/events", json=_evt(event_id="c1", event_type="payment_initiated", transaction_id=tid)).json()[
        "accepted"
    ]
    assert client.post(
        "/events",
        json=_evt(
            event_id="c2",
            event_type="payment_processed",
            transaction_id=tid,
            ts="2026-01-01T00:01:00+00:00",
        ),
    ).json()["accepted"]
    assert client.post(
        "/events",
        json=_evt(
            event_id="c3",
            event_type="payment_failed",
            transaction_id=tid,
            ts="2026-01-01T00:02:00+00:00",
        ),
    ).json()["accepted"]

    r = client.get("/reconciliation/discrepancies", params={"type": "payment_terminal_conflict"})
    assert r.status_code == 200
    ids = {row["transaction_id"] for row in r.json()["items"]}
    assert tid in ids


def test_processed_not_settled_in_discrepancies(client: TestClient) -> None:
    tid = "t-proc-noset"
    assert client.post("/events", json=_evt(event_id="p1", event_type="payment_initiated", transaction_id=tid)).json()[
        "accepted"
    ]
    assert client.post(
        "/events",
        json=_evt(
            event_id="p2",
            event_type="payment_processed",
            transaction_id=tid,
            ts="2026-01-01T00:01:00+00:00",
        ),
    ).json()["accepted"]
    r = client.get("/reconciliation/discrepancies", params={"type": "processed_not_settled"})
    assert r.status_code == 200
    ids = {row["transaction_id"] for row in r.json()["items"]}
    assert tid in ids


def test_settled_without_terminal_in_discrepancies(client: TestClient) -> None:
    tid = "t-sett-no-term"
    assert client.post("/events", json=_evt(event_id="s1", event_type="payment_initiated", transaction_id=tid)).json()[
        "accepted"
    ]
    assert client.post(
        "/events",
        json=_evt(event_id="s2", event_type="settled", transaction_id=tid, ts="2026-01-01T00:01:00+00:00"),
    ).json()["accepted"]
    r = client.get(
        "/reconciliation/discrepancies",
        params={"type": "settled_without_terminal_payment_outcome"},
    )
    assert r.status_code == 200
    ids = {row["transaction_id"] for row in r.json()["items"]}
    assert tid in ids
