from __future__ import annotations

from typing import Optional

from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, func, select

from src.deps import DbSession
from src.models import Event, Transaction
from src.schemas import DiscrepancyListResponse, DiscrepancyOut, DiscrepancySummary, PageMeta, ReconciliationSummaryRow

router = APIRouter(tags=["reconciliation"])

def _day_bucket_expr(db: DbSession):
    """
    Returns a SQL expression that buckets an event timestamp to a day boundary.

    - Postgres: date_trunc('day', ts) -> timestamp
    - SQLite: date(ts) -> YYYY-MM-DD (text)
    """
    bind = getattr(db, "get_bind", None)
    engine = bind() if callable(bind) else getattr(db, "bind", None)
    dialect = getattr(getattr(engine, "dialect", None), "name", None)
    if dialect == "sqlite":
        return func.date(Event.occurred_at)
    return func.date_trunc("day", Event.occurred_at)


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


@router.get(
    "/reconciliation/summary",
    response_model=list[ReconciliationSummaryRow],
    summary="Reconciliation summary aggregates",
    description=(
        "Groups transactions that have ≥1 event in the optional time/merchant window. "
        "`txn_count` / `amount_sum` dedupe per transaction; `event_count` counts matching events."
    ),
)
def reconciliation_summary(
    db: DbSession,
    merchant_id: Optional[str] = Query(default=None, description="Restrict events to this merchant"),
    from_date: Optional[datetime] = Query(default=None, description="Event filter: `occurred_at >= from_date`"),
    to_date: Optional[datetime] = Query(default=None, description="Event filter: `occurred_at < to_date`"),
    group_by: str = Query(
        default="merchant",
        pattern="^(merchant|day|payment_status|settlement)$",
        description="Dimension: merchant | day | payment_status | settlement",
    ),
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
        day_expr = _day_bucket_expr(db).label("day")
        txn_day = (
            select(
                Transaction.merchant_id.label("merchant_id"),
                day_expr.label("day"),
                Transaction.transaction_id.label("transaction_id"),
                func.max(Transaction.amount).label("amount"),
            )
            .select_from(Event)
            .join(Transaction, Transaction.transaction_id == Event.transaction_id)
            .where(*event_filters)
            .group_by(Transaction.merchant_id, day_expr, Transaction.transaction_id)
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
            .where(*event_filters, _day_bucket_expr(db) == txn_day.c.day)
            .group_by(txn_day.c.merchant_id, txn_day.c.day)
            .order_by(txn_day.c.day.asc(), txn_day.c.merchant_id.asc())
        )

        rows = db.execute(stmt).all()
        out_rows: list[ReconciliationSummaryRow] = []
        for r in rows:
            if r.day is None:
                day_val = None
            elif isinstance(r.day, datetime):
                day_val = r.day.date().isoformat()
            elif isinstance(r.day, date):
                day_val = r.day.isoformat()
            else:
                # SQLite `date(ts)` returns 'YYYY-MM-DD' as text.
                day_val = str(r.day)
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


@router.get(
    "/reconciliation/discrepancies",
    response_model=DiscrepancyListResponse,
    summary="List reconciliation discrepancies",
    description=(
        "Transactions with any reconciliation flag set. Optional `type` narrows to one category. "
        "`summary.by_type` uses precedence when multiple flags are true; `summary.total` matches filtered rows."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Invalid `type` query value"},
    },
)
def reconciliation_discrepancies(
    db: DbSession,
    merchant_id: Optional[str] = Query(default=None, description="Filter by merchant"),
    type: Optional[str] = Query(
        default=None,
        description=(
            "Optional: processed_not_settled | settled_after_failed | payment_terminal_conflict | "
            "settled_without_terminal_payment_outcome"
        ),
    ),
    limit: int = Query(default=200, ge=1, le=2000, description="Page size (max 2000)"),
    offset: int = Query(default=0, ge=0),
) -> DiscrepancyListResponse:
    any_discrepancy = (
        Transaction.payment_conflict.is_(True)
        | Transaction.recon_processed_not_settled.is_(True)
        | Transaction.recon_settled_without_processed.is_(True)
        | Transaction.recon_settled_after_failed.is_(True)
    )

    base_filters: list[object] = [any_discrepancy]
    if merchant_id is not None:
        base_filters.append(Transaction.merchant_id == merchant_id)

    type_exprs = {
        "payment_terminal_conflict": Transaction.payment_conflict.is_(True),
        "processed_not_settled": Transaction.recon_processed_not_settled.is_(True),
        "settled_without_terminal_payment_outcome": Transaction.recon_settled_without_processed.is_(True),
        "settled_after_failed": Transaction.recon_settled_after_failed.is_(True),
    }
    page_filters = list(base_filters)
    if type is not None:
        if type not in type_exprs:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid discrepancy type")
        page_filters.append(type_exprs[type])

    total_matching = int(
        db.scalar(select(func.count()).select_from(select(Transaction.transaction_id).where(*page_filters).subquery())) or 0
    )
    by_type: dict[str, int] = {k: 0 for k in type_exprs.keys()}

    # Bucket counts using a single precedence expression so numbers don't double-count
    # when a row matches multiple boolean flags.
    primary = case(
        (Transaction.payment_conflict.is_(True), "payment_terminal_conflict"),
        (Transaction.recon_processed_not_settled.is_(True), "processed_not_settled"),
        (Transaction.recon_settled_after_failed.is_(True), "settled_after_failed"),
        (Transaction.recon_settled_without_processed.is_(True), "settled_without_terminal_payment_outcome"),
        else_="unknown",
    )
    bucket_rows = db.execute(select(primary.label("kind"), func.count().label("n")).where(*base_filters).group_by(primary)).all()
    for kind, n in bucket_rows:
        if kind in by_type:
            by_type[kind] = int(n or 0)

    rows = db.scalars(
        select(Transaction).where(*page_filters).order_by(Transaction.updated_at.desc()).limit(limit).offset(offset)
    ).all()

    items = [
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

    return DiscrepancyListResponse(
        items=items,
        summary=DiscrepancySummary(total=total_matching, by_type=by_type),
        page=PageMeta(limit=limit, offset=offset, total=total_matching),
    )
