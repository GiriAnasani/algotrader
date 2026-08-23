"""Broker-independent domain model for one confirmed managed position."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from numbers import Real


class PositionSide(Enum):
    """Supported NIFTY option sides."""

    CE = "CE"
    PE = "PE"


class PositionState(Enum):
    """Lifecycle state of a confirmed position."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class ManagedPosition:
    """Validated immutable state for one bot-owned confirmed position."""

    side: PositionSide
    contract_symbol: str
    quantity: int
    entry_price: float
    entry_time: datetime
    state: PositionState

    def __post_init__(self):
        if not isinstance(self.side, PositionSide):
            raise ValueError("Side must be a PositionSide.")

        if not isinstance(self.contract_symbol, str) or not self.contract_symbol.strip():
            raise ValueError("Contract symbol must be a non-empty string.")

        contract_symbol = self.contract_symbol.strip()
        if not contract_symbol.upper().endswith(self.side.value):
            raise ValueError("Contract symbol suffix must match the position side.")

        if (
            isinstance(self.quantity, bool)
            or not isinstance(self.quantity, int)
            or self.quantity <= 0
        ):
            raise ValueError("Quantity must be a positive integer.")

        if (
            isinstance(self.entry_price, bool)
            or not isinstance(self.entry_price, Real)
            or not isfinite(self.entry_price)
            or self.entry_price < 0
        ):
            raise ValueError("Entry price must be a finite non-negative number.")

        if not isinstance(self.entry_time, datetime):
            raise ValueError("Entry time must be a datetime.")
        if self.entry_time.tzinfo is None or self.entry_time.utcoffset() is None:
            raise ValueError("Entry time must be timezone-aware.")

        if not isinstance(self.state, PositionState):
            raise ValueError("State must be a PositionState.")

        object.__setattr__(self, "contract_symbol", contract_symbol)
        object.__setattr__(self, "entry_price", float(self.entry_price))
