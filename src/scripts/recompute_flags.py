from __future__ import annotations

import argparse

from sqlalchemy import select

from src.db import SessionLocal
from src.models import Event, Transaction
from src.schemas import EventIn
from src.services.ingestion import (
    apply_payment_lifecycle,
    normalize_ts,
    refresh_reconciliation_flags,
    reset_transaction_derived_state,
)


def recompute(*, commit_every: int = 250) -> tuple[int, int]:
    """
    Rebuild transaction derived state from immutable events.

    Useful after bulk loads / backfills where you want to guarantee reconciliation flags
    match current event history.
    """
    db = SessionLocal()
    txns = 0
    events = 0
    try:
        txn_ids = db.scalars(select(Event.transaction_id).distinct().order_by(Event.transaction_id.asc())).all()
        for i, txn_id in enumerate(txn_ids, start=1):
            txn = db.get(Transaction, txn_id)
            if txn is None:
                # Invariants should prevent this, but be defensive for partial datasets.
                continue

            reset_transaction_derived_state(txn)

            rows = db.scalars(
                select(Event)
                .where(Event.transaction_id == txn_id)
                .order_by(Event.occurred_at.asc(), Event.event_id.asc())
            ).all()

            for e in rows:
                body = EventIn.model_validate(
                    {
                        "event_id": e.event_id,
                        "event_type": e.event_type,
                        "transaction_id": e.transaction_id,
                        "merchant_id": e.merchant_id,
                        "merchant_name": txn.merchant.merchant_name if txn.merchant is not None else "",
                        "amount": float(e.amount),
                        "currency": e.currency,
                        "timestamp": normalize_ts(e.occurred_at),
                    }
                )
                apply_payment_lifecycle(txn, body)
                events += 1

            refresh_reconciliation_flags(txn)
            txns += 1

            if i % commit_every == 0:
                db.commit()

        db.commit()
        return txns, events
    finally:
        db.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Recompute derived transaction state from event history.")
    p.add_argument("--commit-every", type=int, default=250)
    args = p.parse_args()

    txns, events = recompute(commit_every=args.commit_every)
    print(f"recomputed_transactions={txns} replayed_events={events}")


if __name__ == "__main__":
    main()

