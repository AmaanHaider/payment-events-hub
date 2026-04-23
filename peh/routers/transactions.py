from __future__ import annotations

from typing import Optional

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import and_, func, select

from peh.deps import DbSession
from peh.models import Event, Merchant, Transaction
from peh.schemas import (
    EventOut,
    MerchantOut,
    PageMeta,
    SortDir,
    SortField,
    TransactionDetailOut,
    TransactionListResponse,
    TransactionOut,
)

router = APIRouter(tags=["transactions"])


def _txn_sort_column(sort: SortField):
    mapping = {
        "updated_at": Transaction.updated_at,
        "created_at": Transaction.created_at,
        "amount": Transaction.amount,
        "payment_status": Transaction.payment_status,
        "settled_at": Transaction.settled_at,
        "transaction_id": Transaction.transaction_id,
    }
    return mapping[sort]


@router.get("/transactions", response_model=TransactionListResponse)
def list_transactions(
    db: DbSession,
    merchant_id: Optional[str] = None,
    status: Optional[str] = Query(default=None, description="Matches transactions.payment_status"),
    from_date: Optional[datetime] = Query(default=None),
    to_date: Optional[datetime] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort: SortField = "updated_at",
    dir: SortDir = Query(default="desc", alias="direction"),
) -> TransactionListResponse:
    txn_filters = []
    if merchant_id is not None:
        txn_filters.append(Transaction.merchant_id == merchant_id)
    if status is not None:
        txn_filters.append(Transaction.payment_status == status)

    event_filters = []
    if from_date is not None:
        event_filters.append(Event.occurred_at >= from_date)
    if to_date is not None:
        event_filters.append(Event.occurred_at < to_date)

    stmt = select(Transaction)

    if event_filters:
        stmt = stmt.where(
            Transaction.transaction_id.in_(select(Event.transaction_id).where(and_(*event_filters)).distinct())
        )

    if txn_filters:
        stmt = stmt.where(and_(*txn_filters))

    sort_col = _txn_sort_column(sort)
    order_cols = [sort_col.desc() if dir == "desc" else sort_col.asc(), Transaction.transaction_id.asc()]

    filtered_stmt = stmt
    count_stmt = select(func.count()).select_from(filtered_stmt.subquery())
    total = int(db.scalar(count_stmt) or 0)

    page_stmt = filtered_stmt.order_by(*order_cols).limit(limit).offset(offset)

    rows = db.scalars(page_stmt).all()

    items = [
        TransactionOut(
            transaction_id=t.transaction_id,
            merchant_id=t.merchant_id,
            amount=float(t.amount),
            currency=t.currency,
            payment_status=t.payment_status,
            has_settlement=t.has_settlement,
            settled_at=t.settled_at,
            payment_conflict=t.payment_conflict,
            updated_at=t.updated_at,
        )
        for t in rows
    ]

    return TransactionListResponse(items=items, page=PageMeta(limit=limit, offset=offset, total=total))


@router.get("/transactions/{transaction_id}", response_model=TransactionDetailOut)
def get_transaction(db: DbSession, transaction_id: str) -> TransactionDetailOut:
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="transaction not found")

    merchant = db.get(Merchant, txn.merchant_id)
    if merchant is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="merchant missing")

    events = db.scalars(
        select(Event).where(Event.transaction_id == transaction_id).order_by(Event.occurred_at.asc(), Event.event_id.asc())
    ).all()

    return TransactionDetailOut(
        transaction_id=txn.transaction_id,
        merchant_id=txn.merchant_id,
        amount=float(txn.amount),
        currency=txn.currency,
        payment_status=txn.payment_status,
        has_settlement=txn.has_settlement,
        settled_at=txn.settled_at,
        payment_conflict=txn.payment_conflict,
        updated_at=txn.updated_at,
        merchant=MerchantOut.model_validate(merchant),
        events=[EventOut.model_validate(e) for e in events],
    )
