from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.db import SessionLocal
from src.models import Event, Transaction
from src.schemas import EventIn
from src.services.ingestion import ingest_payment_event


@pytest.mark.skipif(
    not os.getenv("RUN_FULL_DATASET_TEST"),
    reason="Set RUN_FULL_DATASET_TEST=1 to run the full 10K-event dataset test.",
)
def test_full_dataset_invariants_hold() -> None:
    """
    Opt-in end-to-end-ish correctness test using the provided 10K+ dataset.

    This intentionally asserts dataset-derived invariants (unique IDs, counts),
    plus a few sanity checks on discrepancies, without relying on hardcoded
    discrepancy distributions.
    """
    root = Path(__file__).resolve().parents[1]
    data_path = root / "sample_data" / "sample_events.json"
    raw_events = json.loads(data_path.read_text(encoding="utf-8"))
    assert isinstance(raw_events, list)
    assert len(raw_events) >= 10_000

    unique_event_ids = {e["event_id"] for e in raw_events}
    unique_txn_ids = {e["transaction_id"] for e in raw_events}
    unique_merchants = {e["merchant_id"] for e in raw_events}

    assert len(unique_merchants) >= 3

    db = SessionLocal()
    try:
        accepted = 0
        duplicates = 0
        for obj in raw_events:
            body = EventIn.model_validate(obj)
            res = ingest_payment_event(db, body)
            if res.accepted:
                accepted += 1
            else:
                duplicates += 1
                db.rollback()
            if (accepted + duplicates) % 500 == 0:
                db.commit()
        db.commit()

        persisted_events = db.query(Event).count()
        persisted_txns = db.query(Transaction).count()

        # Dataset-derived invariants
        assert persisted_events == len(unique_event_ids)
        assert persisted_txns == len(unique_txn_ids)

        # Sanity: at least some discrepancies exist in realistic data
        discrepant = (
            db.query(Transaction)
            .filter(
                Transaction.payment_conflict.is_(True)
                | Transaction.recon_processed_not_settled.is_(True)
                | Transaction.recon_settled_without_processed.is_(True)
                | Transaction.recon_settled_after_failed.is_(True)
            )
            .count()
        )
        assert discrepant > 0
        assert discrepant <= persisted_txns
    finally:
        db.close()

