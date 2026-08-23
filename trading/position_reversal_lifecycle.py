"""Apply confirmed close-then-open reversal truth to managed state."""

from dataclasses import dataclass

from trading.close_position_lifecycle import (
    ClosedPosition,
    ClosePositionLifecycle,
    ConfirmedPositionExit,
    PositionExitMismatchError,
)
from trading.open_position_lifecycle import ConfirmedPositionEntry, OpenPositionLifecycle
from trading.position import ManagedPosition, PositionState
from trading.position_manager import NoActivePositionError, PositionManager


@dataclass(frozen=True)
class ConfirmedPositionReversal:
    """Validated pair of already confirmed exit and opposite entry fills."""

    confirmed_exit: ConfirmedPositionExit
    confirmed_entry: ConfirmedPositionEntry

    def __post_init__(self):
        if not isinstance(self.confirmed_exit, ConfirmedPositionExit):
            raise TypeError("Confirmed exit must be a ConfirmedPositionExit.")
        if not isinstance(self.confirmed_entry, ConfirmedPositionEntry):
            raise TypeError("Confirmed entry must be a ConfirmedPositionEntry.")


@dataclass(frozen=True)
class PositionReversalResult:
    """The exact closed and opened position objects from a reversal."""

    closed_position: ClosedPosition
    opened_position: ManagedPosition

    def __post_init__(self):
        if not isinstance(self.closed_position, ClosedPosition):
            raise TypeError("Closed position must be a ClosedPosition.")
        if not isinstance(self.opened_position, ManagedPosition):
            raise TypeError("Opened position must be a ManagedPosition.")
        if self.closed_position.state is not PositionState.CLOSED:
            raise ValueError("Closed position must have CLOSED state.")
        if self.opened_position.state is not PositionState.OPEN:
            raise ValueError("Opened position must have OPEN state.")
        if self.opened_position.side is self.closed_position.side:
            raise ValueError("Reversal positions must have opposite sides.")
        if self.opened_position.entry_time < self.closed_position.exit_time:
            raise ValueError("Opened position must not precede the confirmed exit.")


class InvalidPositionReversalError(RuntimeError):
    """Raised when confirmed fills do not describe a valid reversal."""


class PositionReversalLifecycle:
    """Close current exposure and open confirmed opposite exposure."""

    def __init__(self, position_manager):
        if not isinstance(position_manager, PositionManager):
            raise TypeError("Position manager must be a PositionManager.")
        self._position_manager = position_manager
        self._close_lifecycle = ClosePositionLifecycle(position_manager)
        self._open_lifecycle = OpenPositionLifecycle(position_manager)

    def reverse(self, confirmed_reversal):
        """Apply a fully confirmed reversal and return both lifecycle results."""
        if not isinstance(confirmed_reversal, ConfirmedPositionReversal):
            raise TypeError(
                "Confirmed reversal must be a ConfirmedPositionReversal."
            )

        active_position = self._position_manager.active_position
        if active_position is None:
            raise NoActivePositionError("No active position exists.")

        confirmed_exit = confirmed_reversal.confirmed_exit
        confirmed_entry = confirmed_reversal.confirmed_entry
        if (
            confirmed_exit.side is not active_position.side
            or confirmed_exit.contract_symbol != active_position.contract_symbol
            or confirmed_exit.quantity != active_position.quantity
        ):
            raise PositionExitMismatchError(
                "Confirmed exit must match the active position exactly."
            )
        if confirmed_entry.side is active_position.side:
            raise InvalidPositionReversalError(
                "Confirmed entry must be on the opposite position side."
            )
        if confirmed_entry.fill_time < confirmed_exit.fill_time:
            raise InvalidPositionReversalError(
                "Confirmed entry must not precede the confirmed exit."
            )

        closed_position = self._close_lifecycle.close(confirmed_exit)
        opened_position = self._open_lifecycle.open(confirmed_entry)
        return PositionReversalResult(closed_position, opened_position)
