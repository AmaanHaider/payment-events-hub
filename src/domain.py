from __future__ import annotations

from enum import Enum

try:
    from enum import StrEnum as StdStrEnum  # Python 3.11+
except ImportError:  # Python 3.9/3.10

    class StdStrEnum(str, Enum):
        """Small backport of enum.StrEnum for Python <3.11."""


class EventType(StdStrEnum):
    PAYMENT_INITIATED = "payment_initiated"
    PAYMENT_PROCESSED = "payment_processed"
    PAYMENT_FAILED = "payment_failed"
    SETTLED = "settled"


TERMINAL_PAYMENT_EVENT_TYPES: frozenset[EventType] = frozenset(
    {EventType.PAYMENT_PROCESSED, EventType.PAYMENT_FAILED}
)


class PaymentStatus(StdStrEnum):
    INITIATED = "initiated"
    PROCESSED = "processed"
    FAILED = "failed"
