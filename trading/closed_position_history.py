"""Append-only process-lifetime history of exact closed-position objects."""

from trading.close_position_lifecycle import ClosedPosition


class DuplicateClosedPositionError(RuntimeError):
    """Raised when the same exact closed-position object is recorded twice."""


class ClosedPositionHistory:
    """Own confirmed LIVE closed positions for the current process lifetime."""

    def __init__(self):
        self._positions = []

    @property
    def positions(self):
        return tuple(self._positions)

    def record(self, closed_position):
        if not isinstance(closed_position, ClosedPosition):
            raise TypeError("Closed position must be a ClosedPosition.")
        if any(position is closed_position for position in self._positions):
            raise DuplicateClosedPositionError(
                "The same closed-position object is already recorded."
            )
        self._positions.append(closed_position)
        return closed_position
