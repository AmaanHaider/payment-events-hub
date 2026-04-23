from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.domain import EventType


class EventIn(BaseModel):
    event_id: str = Field(description="Immutable idempotency key; duplicates rejected or flagged as conflict")
    event_type: EventType = Field(description="payment_initiated | payment_processed | payment_failed | settled")
    transaction_id: str = Field(description="Groups events on one transaction row")
    merchant_id: str = Field(description="Merchant FK; upserts merchant name")
    merchant_name: str = Field(description="Stored on merchant row")
    amount: float = Field(gt=0, description="Must be strictly positive")
    currency: str = Field(min_length=3, max_length=3, description="ISO 4217-style 3-letter code; normalized to uppercase")
    timestamp: datetime = Field(description="Event time (ISO 8601); used for ordering and lifecycle")

    @field_validator("currency")
    @classmethod
    def _normalize_currency(cls, v: str) -> str:
        return v.strip().upper()


class EventIngestResponse(BaseModel):
    accepted: bool = Field(description="True when a new event row was persisted")
    duplicate: bool = Field(False, description="True when event_id already existed")
    transaction_id: str = Field(description="Echo of request")
    message: Optional[str] = Field(None, description="Human-readable reason when not accepted")
    conflict: bool = Field(False, description="True when duplicate event_id but payload differs from stored event")
    conflict_fields: Optional[list[str]] = Field(
        None,
        description="Which fields differed vs stored event (e.g. amount, timestamp)",
    )


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
    limit: int = Field(description="Page size requested")
    offset: int = Field(description="Rows skipped")
    total: int = Field(description="Total matching rows for this query")


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


class DiscrepancySummary(BaseModel):
    total: int
    by_type: dict[str, int]


class DiscrepancyListResponse(BaseModel):
    items: list[DiscrepancyOut]
    summary: DiscrepancySummary
    page: PageMeta
