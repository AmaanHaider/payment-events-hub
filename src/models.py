from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Merchant(Base):
    __tablename__ = "merchants"

    merchant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    merchant_name: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    transactions: Mapped[list[Transaction]] = relationship(back_populates="merchant")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_merchant_id", "merchant_id"),
        Index("ix_transactions_payment_status", "payment_status"),
        Index("ix_transactions_updated_at", "updated_at"),
        Index("ix_transactions_has_settlement", "has_settlement"),
        Index("ix_transactions_settled_at", "settled_at"),
    )

    transaction_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    merchant_id: Mapped[str] = mapped_column(String(64), ForeignKey("merchants.merchant_id"), nullable=False)

    amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Latest non-settlement payment lifecycle status (initiated/processed/failed).
    payment_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    last_payment_event_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_payment_event_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Terminal payment outcome tracking (processed vs failed), including out-of-order handling.
    terminal_payment_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    terminal_payment_event_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_payment_event_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    payment_conflict: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")

    has_settlement: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    settlement_event_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Precomputed discrepancy flags for fast filtering; refreshed on every accepted ingest.
    recon_processed_not_settled: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    recon_settled_without_processed: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    recon_settled_after_failed: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")

    merchant: Mapped[Merchant] = relationship(back_populates="transactions")
    events: Mapped[list[Event]] = relationship(back_populates="transaction")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_transaction_occurred_at", "transaction_id", "occurred_at"),
        Index("ix_events_merchant_occurred_at", "merchant_id", "occurred_at"),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)

    transaction_id: Mapped[str] = mapped_column(String(64), ForeignKey("transactions.transaction_id"), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(64), ForeignKey("merchants.merchant_id"), nullable=False)

    amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    transaction: Mapped[Transaction] = relationship(back_populates="events")
