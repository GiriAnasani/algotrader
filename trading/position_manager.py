"""In-memory authority for one confirmed active managed position."""

from trading.position import ManagedPosition, PositionState


class PositionManagerError(RuntimeError):
    """Base error for invalid position-manager operations."""


class ActivePositionExistsError(PositionManagerError):
    """Raised when registration would replace an active position."""


class NoActivePositionError(PositionManagerError):
    """Raised when clearing a manager that has no active position."""


class PositionManager:
    """Authoritative in-memory owner of at most one open position."""

    def __init__(self):
        self._active_position = None

    @property
    def active_position(self):
        """Return the active position, or None when no position is owned."""
        return self._active_position

    @property
    def has_active_position(self):
        """Return whether an active position is currently owned."""
        return self._active_position is not None

    def register(self, position):
        """Register an open position without replacing existing exposure."""
        if not isinstance(position, ManagedPosition):
            raise TypeError("Position must be a ManagedPosition.")
        if position.state is not PositionState.OPEN:
            raise ValueError("Only an open position may be registered as active.")
        if self._active_position is not None:
            raise ActivePositionExistsError("An active position already exists.")

        self._active_position = position

    def clear(self):
        """Remove and return the active position."""
        if self._active_position is None:
            raise NoActivePositionError("No active position exists.")

        position = self._active_position
        self._active_position = None
        return position
