"""Broker-independent immutable status for a submitted broker order."""

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from numbers import Real


class BrokerOrderState(Enum):
    """Normalized broker order lifecycle states."""

    SUBMITTED = "SUBMITTED"
    OPEN = "OPEN"
    COMPLETE = "COMPLETE"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class BrokerOrderStatus:
    """Validated current broker-reported order state and fill quantities."""

    order_id: str
    state: BrokerOrderState
    filled_quantity: int
    pending_quantity: int
    average_price: float
    broker_status: str
    status_message: str | None = None

    def __post_init__(self):
        self._validate_non_empty_string(self.order_id, "Order ID")

        if not isinstance(self.state, BrokerOrderState):
            raise TypeError("State must be a BrokerOrderState.")

        self._validate_quantity(self.filled_quantity, "Filled quantity")
        self._validate_quantity(self.pending_quantity, "Pending quantity")

        if (
            isinstance(self.average_price, bool)
            or not isinstance(self.average_price, Real)
            or not isfinite(self.average_price)
            or self.average_price < 0
        ):
            raise ValueError("Average price must be a finite non-negative number.")

        self._validate_non_empty_string(self.broker_status, "Broker status")

        if self.status_message is not None and not isinstance(
            self.status_message,
            str,
        ):
            raise TypeError("Status message must be a string or None.")

        object.__setattr__(self, "order_id", self.order_id.strip())
        object.__setattr__(self, "average_price", float(self.average_price))
        object.__setattr__(self, "broker_status", self.broker_status.strip())

    @property
    def is_terminal(self):
        return self.state in (
            BrokerOrderState.COMPLETE,
            BrokerOrderState.REJECTED,
            BrokerOrderState.CANCELLED,
        )

    @property
    def is_filled(self):
        return (
            self.state is BrokerOrderState.COMPLETE
            and self.filled_quantity > 0
            and self.pending_quantity == 0
        )

    @property
    def is_rejected(self):
        return self.state is BrokerOrderState.REJECTED

    @staticmethod
    def _validate_non_empty_string(value, label):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string.")

    @staticmethod
    def _validate_quantity(value, label):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise ValueError(f"{label} must be a non-negative integer.")
