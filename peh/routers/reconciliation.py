from __future__ import annotations

from typing import Optional

from datetime import datetime

from fastapi import APIRouter, Query
from sqlalchemy import case, func, select

from peh.deps import DbSession
from peh.models import Event, Transaction
from peh.schemas import DiscrepancyOut, ReconciliationSummaryRow

router = APIRouter(tags=["reconciliation"])


def _discrepancy_types(t: Transaction) -> list[str]:
    types: list[str] = []
    if t.payment_conflict:
        types.append("payment_terminal_conflict")
    if t.recon_processed_not_settled:
        types.append("processed_not_settled")
    if t.recon_settled_without_processed:
        types.append("settled_without_terminal_payment_outcome")
    if t.recon_settled_after_failed:
        types.append("settled_after_failed")
    return types


@router.get("/reconciliation/summary", response_model=list[ReconciliationSummaryRow])
def reconciliation_summary(
    db: DbSession,
    merchant_id: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    group_by: str = Query(default="merchant", pattern="^(merchant|day|payment_status|settlement)$"),
) -> list[ReconciliationSummaryRow]:
    """
    Summaries are computed over the set of transactions that have >=1 event in the optional time window.

    - txn_count / amount_sum are de-duplicated per transaction
    - event_count counts events matching the same filters (can be >1 per txn)
    """
    event_filters = []
    if merchant_id is not None:
        event_filters.append(Event.merchant_id == merchant_id)
    if from_date is not None:
        event_filters.append(Event.occurred_at >= from_date)
    if to_date is not None:
        event_filters.append(Event.occurred_at < to_date)

    if event_filters:
        window = select(Event.transaction_id).where(*event_filters).distinct().subquery()
    else:
        window = select(Event.transaction_id).distinct().subquery()
    settlement_state = case((Transaction.has_settlement.is_(True), "settled"), else_="unsettled")

    if group_by == "merchant":
        txn_keys = (
            select(Transaction.merchant_id, Transaction.transaction_id, Transaction.amount)
            .join(window, window.c.transaction_id == Transaction.transaction_id)
            .distinct()
            .subquery()
        )
        stmt = (
            select(
                txn_keys.c.merchant_id.label("merchant_id"),
                func.count().label("txn_count"),
                func.coalesce(func.sum(txn_keys.c.amount), 0).label("amount_sum"),
                func.count(Event.event_id).label("event_count"),
            )
            .select_from(txn_keys)
            .join(Event, Event.transaction_id == txn_keys.c.transaction_id)
            .where(*event_filters)
            .group_by(txn_keys.c.merchant_id)
            .order_by(txn_keys.c.merchant_id.asc())
        )
        rows = db.execute(stmt).all()
        return [
            ReconciliationSummaryRow(
                merchant_id=r.merchant_id,
                txn_count=int(r.txn_count),
                event_count=int(r.event_count),
                amount_sum=float(r.amount_sum),
            )
            for r in rows
        ]

    if group_by == "day":
        day = func.date_trunc("day", Event.occurred_at).label("day")
        txn_day = (
            select(
                Transaction.merchant_id.label("merchant_id"),
                day.label("day"),
                Transaction.transaction_id.label("transaction_id"),
                func.max(Transaction.amount).label("amount"),
            )
            .select_from(Event)
            .join(Transaction, Transaction.transaction_id == Event.transaction_id)
            .where(*event_filters)
            .group_by(Transaction.merchant_id, day, Transaction.transaction_id)
            .subquery()
        )

        stmt = (
            select(
                txn_day.c.merchant_id.label("merchant_id"),
                txn_day.c.day.label("day"),
                func.count().label("txn_count"),
                func.coalesce(func.sum(txn_day.c.amount), 0).label("amount_sum"),
                func.count(Event.event_id).label("event_count"),
            )
            .select_from(txn_day)
            .join(Event, Event.transaction_id == txn_day.c.transaction_id)
            .where(*event_filters, func.date_trunc("day", Event.occurred_at) == txn_day.c.day)
            .group_by(txn_day.c.merchant_id, txn_day.c.day)
            .order_by(txn_day.c.day.asc(), txn_day.c.merchant_id.asc())
        )

        rows = db.execute(stmt).all()
        out_rows: list[ReconciliationSummaryRow] = []
        for r in rows:
            day_val = r.day.date().isoformat() if r.day is not None else None
            out_rows.append(
                ReconciliationSummaryRow(
                    merchant_id=r.merchant_id,
                    day=day_val,
                    txn_count=int(r.txn_count),
                    event_count=int(r.event_count),
                    amount_sum=float(r.amount_sum),
                )
            )
        return out_rows

    if group_by == "payment_status":
        txn_keys = (
            select(Transaction.merchant_id, Transaction.transaction_id, Transaction.amount, Transaction.payment_status)
            .join(window, window.c.transaction_id == Transaction.transaction_id)
            .distinct()
            .subquery()
        )
        stmt = (
            select(
                txn_keys.c.merchant_id.label("merchant_id"),
                txn_keys.c.payment_status.label("payment_status"),
                func.count().label("txn_count"),
                func.coalesce(func.sum(txn_keys.c.amount), 0).label("amount_sum"),
                func.count(Event.event_id).label("event_count"),
            )
            .select_from(txn_keys)
            .join(Event, Event.transaction_id == txn_keys.c.transaction_id)
            .where(*event_filters)
            .group_by(txn_keys.c.merchant_id, txn_keys.c.payment_status)
            .order_by(txn_keys.c.merchant_id.asc(), txn_keys.c.payment_status.asc().nullsfirst())
        )
        rows = db.execute(stmt).all()
        return [
            ReconciliationSummaryRow(
                merchant_id=r.merchant_id,
                payment_status=r.payment_status,
                txn_count=int(r.txn_count),
                event_count=int(r.event_count),
                amount_sum=float(r.amount_sum),
            )
            for r in rows
        ]

    if group_by == "settlement":
        txn_keys = (
            select(
                Transaction.merchant_id,
                Transaction.transaction_id,
                Transaction.amount,
                settlement_state.label("settlement_state"),
            )
            .join(window, window.c.transaction_id == Transaction.transaction_id)
            .distinct()
            .subquery()
        )
        stmt = (
            select(
                txn_keys.c.merchant_id.label("merchant_id"),
                txn_keys.c.settlement_state.label("settlement_state"),
                func.count().label("txn_count"),
                func.coalesce(func.sum(txn_keys.c.amount), 0).label("amount_sum"),
                func.count(Event.event_id).label("event_count"),
            )
            .select_from(txn_keys)
            .join(Event, Event.transaction_id == txn_keys.c.transaction_id)
            .where(*event_filters)
            .group_by(txn_keys.c.merchant_id, txn_keys.c.settlement_state)
            .order_by(txn_keys.c.merchant_id.asc(), txn_keys.c.settlement_state.asc())
        )
        rows = db.execute(stmt).all()
        return [
            ReconciliationSummaryRow(
                merchant_id=r.merchant_id,
                settlement_state=r.settlement_state,
                txn_count=int(r.txn_count),
                event_count=int(r.event_count),
                amount_sum=float(r.amount_sum),
            )
            for r in rows
        ]

    raise AssertionError("unreachable group_by")


@router.get("/reconciliation/discrepancies", response_model=list[DiscrepancyOut])
def reconciliation_discrepancies(
    db: DbSession,
    merchant_id: Optional[str] = None,
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> list[DiscrepancyOut]:
    filters = [
        (
            Transaction.payment_conflict.is_(True)
            | Transaction.recon_processed_not_settled.is_(True)
            | Transaction.recon_settled_without_processed.is_(True)
            | Transaction.recon_settled_after_failed.is_(True)
        )
    ]
    if merchant_id is not None:
        filters.append(Transaction.merchant_id == merchant_id)

    rows = db.scalars(
        select(Transaction).where(*filters).order_by(Transaction.updated_at.desc()).limit(limit).offset(offset)
    ).all()

    return [
        DiscrepancyOut(
            transaction_id=t.transaction_id,
            merchant_id=t.merchant_id,
            payment_status=t.payment_status,
            terminal_payment_status=t.terminal_payment_status,
            has_settlement=t.has_settlement,
            settled_at=t.settled_at,
            payment_conflict=t.payment_conflict,
            recon_processed_not_settled=t.recon_processed_not_settled,
            recon_settled_without_processed=t.recon_settled_without_processed,
            recon_settled_after_failed=t.recon_settled_after_failed,
            discrepancy_types=_discrepancy_types(t),
        )
        for t in rows
    ]
