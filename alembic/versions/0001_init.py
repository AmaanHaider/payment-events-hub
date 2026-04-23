"""initial schema

Revision ID: 0001_init
Revises: 
Create Date: 2026-04-24

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "merchants",
        sa.Column("merchant_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("merchant_name", sa.String(length=255), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )

    op.create_table(
        "transactions",
        sa.Column("transaction_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("merchant_id", sa.String(length=64), sa.ForeignKey("merchants.merchant_id"), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("payment_status", sa.String(length=32), nullable=True),
        sa.Column("last_payment_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_payment_event_id", sa.String(length=64), nullable=True),
        sa.Column("terminal_payment_status", sa.String(length=32), nullable=True),
        sa.Column("terminal_payment_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_payment_event_id", sa.String(length=64), nullable=True),
        sa.Column("payment_conflict", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("has_settlement", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settlement_event_id", sa.String(length=64), nullable=True),
        sa.Column("recon_processed_not_settled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("recon_settled_without_processed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("recon_settled_after_failed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.create_index("ix_transactions_merchant_id", "transactions", ["merchant_id"])
    op.create_index("ix_transactions_payment_status", "transactions", ["payment_status"])
    op.create_index("ix_transactions_updated_at", "transactions", ["updated_at"])
    op.create_index("ix_transactions_has_settlement", "transactions", ["has_settlement"])
    op.create_index("ix_transactions_settled_at", "transactions", ["settled_at"])

    op.create_table(
        "events",
        sa.Column("event_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("transaction_id", sa.String(length=64), sa.ForeignKey("transactions.transaction_id"), nullable=False),
        sa.Column("merchant_id", sa.String(length=64), sa.ForeignKey("merchants.merchant_id"), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_json", sa.Text(), nullable=True),
    )

    op.create_index("ix_events_transaction_occurred_at", "events", ["transaction_id", "occurred_at"])
    op.create_index("ix_events_merchant_occurred_at", "events", ["merchant_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_events_merchant_occurred_at", table_name="events")
    op.drop_index("ix_events_transaction_occurred_at", table_name="events")
    op.drop_table("events")

    op.drop_index("ix_transactions_settled_at", table_name="transactions")
    op.drop_index("ix_transactions_has_settlement", table_name="transactions")
    op.drop_index("ix_transactions_updated_at", table_name="transactions")
    op.drop_index("ix_transactions_payment_status", table_name="transactions")
    op.drop_index("ix_transactions_merchant_id", table_name="transactions")
    op.drop_table("transactions")

    op.drop_table("merchants")

