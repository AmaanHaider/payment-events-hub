from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class EventIn(BaseModel):
    event_id: str
    event_type: str
    transaction_id: str
    merchant_id: str
    merchant_name: str
    amount: float
    currency: str
    timestamp: datetime


class EventIngestResponse(BaseModel):
    accepted: bool
    duplicate: bool = False
    transaction_id: str
    message: Optional[str] = None


class MerchantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    merchant_id: str
    merchant_name: str


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_id: str
    merchant_id: str
    amount: float
    currency: str
    payment_status: Optional[str]
    has_settlement: bool
    settled_at: Optional[datetime]
    payment_conflict: bool
    updated_at: datetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: str
    event_type: str
    transaction_id: str
    merchant_id: str
    amount: float
    currency: str
    occurred_at: datetime


class TransactionDetailOut(TransactionOut):
    merchant: MerchantOut
    events: list[EventOut]


class PageMeta(BaseModel):
    limit: int
    offset: int
    total: int


class TransactionListResponse(BaseModel):
    items: list[TransactionOut]
    page: PageMeta


SortField = Literal[
    "updated_at",
    "created_at",
    "amount",
    "payment_status",
    "settled_at",
    "transaction_id",
]
SortDir = Literal["asc", "desc"]


class ReconciliationSummaryRow(BaseModel):
    merchant_id: Optional[str] = None
    day: Optional[str] = None
    payment_status: Optional[str] = None
    settlement_state: Optional[str] = None
    txn_count: int
    event_count: int
    amount_sum: float


class DiscrepancyOut(BaseModel):
    transaction_id: str
    merchant_id: str
    payment_status: Optional[str]
    terminal_payment_status: Optional[str]
    has_settlement: bool
    settled_at: Optional[datetime]
    payment_conflict: bool
    recon_processed_not_settled: bool
    recon_settled_without_processed: bool
    recon_settled_after_failed: bool
    discrepancy_types: list[str]
