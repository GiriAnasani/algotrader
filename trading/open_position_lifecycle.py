"""Convert confirmed entry truth into an active managed position."""

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from numbers import Real

from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import PositionManager


@dataclass(frozen=True)
class ConfirmedPositionEntry:
    """Broker-independent details of an already confirmed full entry fill."""

    side: PositionSide
    contract_symbol: str
    quantity: int
    fill_price: float
    fill_time: datetime

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
            isinstance(self.fill_price, bool)
            or not isinstance(self.fill_price, Real)
            or not isfinite(self.fill_price)
            or self.fill_price < 0
        ):
            raise ValueError("Fill price must be a finite non-negative number.")

        if not isinstance(self.fill_time, datetime):
            raise ValueError("Fill time must be a datetime.")
        if self.fill_time.tzinfo is None or self.fill_time.utcoffset() is None:
            raise ValueError("Fill time must be timezone-aware.")

        object.__setattr__(self, "contract_symbol", contract_symbol)
        object.__setattr__(self, "fill_price", float(self.fill_price))


class OpenPositionLifecycle:
    """Create and register an open position from confirmed entry truth."""

    def __init__(self, position_manager):
        if not isinstance(position_manager, PositionManager):
            raise TypeError("Position manager must be a PositionManager.")
        self._position_manager = position_manager

    def open(self, confirmed_entry):
        """Create, register, and return the position for a confirmed entry."""
        if not isinstance(confirmed_entry, ConfirmedPositionEntry):
            raise TypeError("Confirmed entry must be a ConfirmedPositionEntry.")

        position = ManagedPosition(
            side=confirmed_entry.side,
            contract_symbol=confirmed_entry.contract_symbol,
            quantity=confirmed_entry.quantity,
            entry_price=confirmed_entry.fill_price,
            entry_time=confirmed_entry.fill_time,
            state=PositionState.OPEN,
        )
        self._position_manager.register(position)
        return position
