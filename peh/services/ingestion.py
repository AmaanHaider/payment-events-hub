from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from peh.domain import TERMINAL_PAYMENT_EVENT_TYPES, EventType, PaymentStatus
from peh.models import Event, Merchant, Transaction
from peh.schemas import EventIn


def normalize_ts(ts: Optional[datetime]) -> Optional[datetime]:
    """Normalize timestamps for comparisons + SQLite round-trips.

    - If tz-aware: convert to UTC
    - If tz-naive: treat as UTC
    """
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _dt_key(ts: datetime, tie_breaker: str) -> tuple[datetime, str]:
    nt = normalize_ts(ts)
    assert nt is not None
    return (nt, tie_breaker)


def _is_strictly_newer(ts_a: datetime, id_a: str, ts_b: Optional[datetime], id_b: Optional[str]) -> bool:
    if ts_b is None or id_b is None:
        return True
    return _dt_key(ts_a, id_a) > _dt_key(ts_b, id_b)


def _is_strictly_older(ts_a: datetime, id_a: str, ts_b: Optional[datetime], id_b: Optional[str]) -> bool:
    if ts_b is None or id_b is None:
        return False
    return _dt_key(ts_a, id_a) < _dt_key(ts_b, id_b)


def _mark_payment_conflict_if_terminal_mismatch(
    txn: Transaction,
    incoming_terminal: PaymentStatus,
    incoming_at: datetime,
    incoming_id: str,
) -> None:
    if txn.terminal_payment_status is None:
        return
    if txn.terminal_payment_event_at is None or txn.terminal_payment_event_id is None:
        return

    existing = PaymentStatus(txn.terminal_payment_status)
    if existing == incoming_terminal:
        return

    if _is_strictly_older(incoming_at, incoming_id, txn.terminal_payment_event_at, txn.terminal_payment_event_id):
        txn.payment_conflict = True
        return

    if _is_strictly_newer(incoming_at, incoming_id, txn.terminal_payment_event_at, txn.terminal_payment_event_id):
        txn.payment_conflict = True
        return

    txn.payment_conflict = True


def _refresh_reconciliation_flags(txn: Transaction) -> None:
    processed_not_settled = bool(txn.terminal_payment_status == PaymentStatus.PROCESSED.value and not txn.has_settlement)

    settled_without_processed = bool(
        txn.has_settlement
        and txn.terminal_payment_status not in {PaymentStatus.PROCESSED.value, PaymentStatus.FAILED.value}
    )

    settled_after_failed = bool(txn.has_settlement and txn.terminal_payment_status == PaymentStatus.FAILED.value)

    txn.recon_processed_not_settled = processed_not_settled
    txn.recon_settled_without_processed = settled_without_processed
    txn.recon_settled_after_failed = settled_after_failed


def _apply_payment_lifecycle(txn: Transaction, event: EventIn) -> None:
    try:
        et = EventType(event.event_type)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown event_type") from e

    if et == EventType.PAYMENT_INITIATED:
        if _is_strictly_newer(event.timestamp, event.event_id, txn.last_payment_event_at, txn.last_payment_event_id):
            txn.last_payment_event_at = event.timestamp
            txn.last_payment_event_id = event.event_id

            if txn.terminal_payment_status is None:
                txn.payment_status = PaymentStatus.INITIATED.value
        return

    if et in TERMINAL_PAYMENT_EVENT_TYPES:
        incoming_terminal = PaymentStatus.PROCESSED if et == EventType.PAYMENT_PROCESSED else PaymentStatus.FAILED

        _mark_payment_conflict_if_terminal_mismatch(txn, incoming_terminal, event.timestamp, event.event_id)

        if _is_strictly_newer(event.timestamp, event.event_id, txn.last_payment_event_at, txn.last_payment_event_id):
            txn.last_payment_event_at = event.timestamp
            txn.last_payment_event_id = event.event_id

        if _is_strictly_newer(
            event.timestamp,
            event.event_id,
            txn.terminal_payment_event_at,
            txn.terminal_payment_event_id,
        ):
            txn.terminal_payment_status = incoming_terminal.value
            txn.terminal_payment_event_at = event.timestamp
            txn.terminal_payment_event_id = event.event_id

            txn.payment_status = incoming_terminal.value

        return

    if et == EventType.SETTLED:
        if not txn.has_settlement:
            txn.has_settlement = True
            txn.settled_at = event.timestamp
            txn.settlement_event_id = event.event_id
            return

        assert txn.settled_at is not None
        assert txn.settlement_event_id is not None

        if _dt_key(event.timestamp, event.event_id) < _dt_key(txn.settled_at, txn.settlement_event_id):
            txn.settled_at = event.timestamp
            txn.settlement_event_id = event.event_id

        return

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown event_type")


@dataclass(frozen=True)
class IngestResult:
    accepted: bool
    duplicate: bool
    message: Optional[str] = None


def ingest_payment_event(db: Session, event_in: EventIn) -> IngestResult:
    event_in = event_in.model_copy(update={"timestamp": normalize_ts(event_in.timestamp)})

    existing = db.get(Event, event_in.event_id)
    if existing is not None:
        return IngestResult(accepted=False, duplicate=True, message="Duplicate event_id")

    txn = db.get(Transaction, event_in.transaction_id)
    if txn is not None and txn.merchant_id != event_in.merchant_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="transaction_id already associated with a different merchant_id",
        )

    merchant = db.get(Merchant, event_in.merchant_id)
    if merchant is None:
        merchant = Merchant(merchant_id=event_in.merchant_id, merchant_name=event_in.merchant_name)
        db.add(merchant)
    else:
        merchant.merchant_name = event_in.merchant_name

    if txn is None:
        txn = Transaction(
            transaction_id=event_in.transaction_id,
            merchant_id=event_in.merchant_id,
            amount=event_in.amount,
            currency=event_in.currency,
            created_at=event_in.timestamp,
            updated_at=event_in.timestamp,
        )
        db.add(txn)
    else:
        txn.amount = event_in.amount
        txn.currency = event_in.currency
        txn.updated_at = event_in.timestamp

    event_row = Event(
        event_id=event_in.event_id,
        event_type=event_in.event_type,
        transaction_id=event_in.transaction_id,
        merchant_id=event_in.merchant_id,
        amount=event_in.amount,
        currency=event_in.currency,
        occurred_at=event_in.timestamp,
        raw_json=json.dumps(event_in.model_dump(mode="json"), default=str),
    )
    db.add(event_row)

    _apply_payment_lifecycle(txn, event_in)
    _refresh_reconciliation_flags(txn)

    return IngestResult(accepted=True, duplicate=False)
