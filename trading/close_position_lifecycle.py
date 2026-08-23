"""Convert confirmed exit truth into an immutable closed position."""

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from numbers import Real

from trading.position import PositionSide, PositionState
from trading.position_manager import NoActivePositionError, PositionManager


def _validate_position_values(
    side, contract_symbol, quantity, price, timestamp, price_label, time_label
):
    if not isinstance(side, PositionSide):
        raise ValueError("Side must be a PositionSide.")
    if not isinstance(contract_symbol, str) or not contract_symbol.strip():
        raise ValueError("Contract symbol must be a non-empty string.")

    normalized_symbol = contract_symbol.strip()
    if not normalized_symbol.upper().endswith(side.value):
        raise ValueError("Contract symbol suffix must match the position side.")

    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("Quantity must be a positive integer.")
    if (
        isinstance(price, bool)
        or not isinstance(price, Real)
        or not isfinite(price)
        or price < 0
    ):
        raise ValueError(f"{price_label} must be a finite non-negative number.")
    if not isinstance(timestamp, datetime):
        raise ValueError(f"{time_label} must be a datetime.")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{time_label} must be timezone-aware.")

    return normalized_symbol, float(price)


@dataclass(frozen=True)
class ConfirmedPositionExit:
    """Broker-independent details of an already confirmed full exit fill."""

    side: PositionSide
    contract_symbol: str
    quantity: int
    fill_price: float
    fill_time: datetime

    def __post_init__(self):
        contract_symbol, fill_price = _validate_position_values(
            self.side,
            self.contract_symbol,
            self.quantity,
            self.fill_price,
            self.fill_time,
            "Fill price",
            "Fill time",
        )
        object.__setattr__(self, "contract_symbol", contract_symbol)
        object.__setattr__(self, "fill_price", fill_price)


@dataclass(frozen=True)
class ClosedPosition:
    """Immutable result of closing one managed position."""

    side: PositionSide
    contract_symbol: str
    quantity: int
    entry_price: float
    entry_time: datetime
    exit_price: float
    exit_time: datetime
    state: PositionState

    def __post_init__(self):
        contract_symbol, entry_price = _validate_position_values(
            self.side,
            self.contract_symbol,
            self.quantity,
            self.entry_price,
            self.entry_time,
            "Entry price",
            "Entry time",
        )
        _, exit_price = _validate_position_values(
            self.side,
            contract_symbol,
            self.quantity,
            self.exit_price,
            self.exit_time,
            "Exit price",
            "Exit time",
        )
        if self.state is not PositionState.CLOSED:
            raise ValueError("State must be PositionState.CLOSED.")
        if self.exit_time < self.entry_time:
            raise ValueError("Exit time must not precede entry time.")

        object.__setattr__(self, "contract_symbol", contract_symbol)
        object.__setattr__(self, "entry_price", entry_price)
        object.__setattr__(self, "exit_price", exit_price)


class PositionExitMismatchError(RuntimeError):
    """Raised when confirmed exit truth does not match active exposure."""


class ClosePositionLifecycle:
    """Validate a confirmed exit and remove the matching active position."""

    def __init__(self, position_manager):
        if not isinstance(position_manager, PositionManager):
            raise TypeError("Position manager must be a PositionManager.")
        self._position_manager = position_manager

    def close(self, confirmed_exit):
        """Create a closed result, then atomically clear active ownership."""
        if not isinstance(confirmed_exit, ConfirmedPositionExit):
            raise TypeError("Confirmed exit must be a ConfirmedPositionExit.")

        active_position = self._position_manager.active_position
        if active_position is None:
            raise NoActivePositionError("No active position exists.")
        if (
            confirmed_exit.side is not active_position.side
            or confirmed_exit.contract_symbol != active_position.contract_symbol
            or confirmed_exit.quantity != active_position.quantity
        ):
            raise PositionExitMismatchError(
                "Confirmed exit must match the active position exactly."
            )

        closed_position = ClosedPosition(
            side=active_position.side,
            contract_symbol=active_position.contract_symbol,
            quantity=active_position.quantity,
            entry_price=active_position.entry_price,
            entry_time=active_position.entry_time,
            exit_price=confirmed_exit.fill_price,
            exit_time=confirmed_exit.fill_time,
            state=PositionState.CLOSED,
        )
        self._position_manager.clear()
        return closed_position
