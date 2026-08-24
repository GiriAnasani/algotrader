"""Deterministic position-level gross P&L calculations."""

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from numbers import Real

from trading.close_position_lifecycle import ClosedPosition
from trading.position import ManagedPosition, PositionSide, PositionState


class PositionPnLState(Enum):
    """Whether gross P&L is valued for open or closed position truth."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


def _validate_positive_price(value, label):
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{label} must be a finite positive number.")
    return float(value)


@dataclass(frozen=True)
class PositionPnLResult:
    """Immutable position-level gross P&L result."""

    state: PositionPnLState
    side: PositionSide
    contract_symbol: str
    quantity: int
    entry_price: float
    reference_price: float
    gross_pnl: float

    def __post_init__(self):
        if not isinstance(self.state, PositionPnLState):
            raise TypeError("State must be a PositionPnLState.")
        if not isinstance(self.side, PositionSide):
            raise TypeError("Side must be a PositionSide.")
        if not isinstance(self.contract_symbol, str) or not self.contract_symbol.strip():
            raise ValueError("Contract symbol must be a non-empty string.")
        if (
            isinstance(self.quantity, bool)
            or not isinstance(self.quantity, int)
            or self.quantity <= 0
        ):
            raise ValueError("Quantity must be a positive integer.")

        entry_price = _validate_positive_price(self.entry_price, "Entry price")
        reference_price = _validate_positive_price(
            self.reference_price, "Reference price"
        )
        if (
            isinstance(self.gross_pnl, bool)
            or not isinstance(self.gross_pnl, Real)
            or not isfinite(self.gross_pnl)
        ):
            raise ValueError("Gross P&L must be a finite number.")

        object.__setattr__(self, "contract_symbol", self.contract_symbol.strip())
        object.__setattr__(self, "entry_price", entry_price)
        object.__setattr__(self, "reference_price", reference_price)
        object.__setattr__(self, "gross_pnl", float(self.gross_pnl))


class PositionPnLCalculator:
    """Calculate gross P&L solely from immutable position values."""

    def calculate_open(self, position, reference_price):
        if not isinstance(position, ManagedPosition):
            raise TypeError("Position must be a ManagedPosition.")
        if position.state is not PositionState.OPEN:
            raise ValueError("Open P&L requires an OPEN ManagedPosition.")

        reference_price = _validate_positive_price(
            reference_price, "Reference price"
        )
        gross_pnl = (reference_price - position.entry_price) * position.quantity
        return PositionPnLResult(
            state=PositionPnLState.OPEN,
            side=position.side,
            contract_symbol=position.contract_symbol,
            quantity=position.quantity,
            entry_price=position.entry_price,
            reference_price=reference_price,
            gross_pnl=gross_pnl,
        )

    def calculate_closed(self, position):
        if not isinstance(position, ClosedPosition):
            raise TypeError("Position must be a ClosedPosition.")

        gross_pnl = (position.exit_price - position.entry_price) * position.quantity
        return PositionPnLResult(
            state=PositionPnLState.CLOSED,
            side=position.side,
            contract_symbol=position.contract_symbol,
            quantity=position.quantity,
            entry_price=position.entry_price,
            reference_price=position.exit_price,
            gross_pnl=gross_pnl,
        )
