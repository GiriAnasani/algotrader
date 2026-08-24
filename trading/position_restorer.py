"""Explicit restoration of an authorized persisted runtime position."""

from dataclasses import dataclass
from enum import Enum

from trading.position import ManagedPosition, PositionState
from trading.position_manager import PositionManager
from trading.position_restart import PositionRestartResult, PositionRestartState
from trading.position_safety import PositionSafetyState


class PositionRestorationError(RuntimeError):
    """Raised when a position restoration attempt is not safely authorized."""


class PositionRestorationState(Enum):
    """Successful position restoration outcomes."""

    RESTORED = "RESTORED"


@dataclass(frozen=True)
class PositionRestorationResult:
    """Immutable record of one successful explicit restoration."""

    state: PositionRestorationState
    restart_result: PositionRestartResult
    restored_position: ManagedPosition
    message: str

    def __post_init__(self):
        if not isinstance(self.state, PositionRestorationState):
            raise TypeError("State must be a PositionRestorationState.")
        if self.state is not PositionRestorationState.RESTORED:
            raise ValueError("Restoration state must be RESTORED.")
        if not isinstance(self.restart_result, PositionRestartResult):
            raise TypeError("Restart result must be a PositionRestartResult.")
        if not isinstance(self.restored_position, ManagedPosition):
            raise TypeError("Restored position must be a ManagedPosition.")
        if self.restart_result.state is not PositionRestartState.RESTORE_ALLOWED:
            raise ValueError("Restart result must allow restoration.")
        if not self.restart_result.can_restore:
            raise ValueError("Restart result must authorize restoration.")
        if self.restored_position.state is not PositionState.OPEN:
            raise ValueError("Restored position must be OPEN.")
        if self.restored_position is not self.restart_result.persisted_position:
            raise ValueError("Restored position must be the exact persisted position.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("Message must be a non-empty string.")

        object.__setattr__(self, "message", self.message.strip())


class PositionRestorer:
    """Restore one explicitly authorized position into a flat manager."""

    def __init__(self, position_manager):
        if not isinstance(position_manager, PositionManager):
            raise TypeError("Position manager must be a PositionManager.")
        self._position_manager = position_manager

    def restore(self, restart_result):
        """Register the exact authorized position after fail-closed checks."""
        if not isinstance(restart_result, PositionRestartResult):
            raise TypeError("Restart result must be a PositionRestartResult.")

        position = restart_result.persisted_position
        synchronization_result = restart_result.recovery_result.synchronization_result
        is_authorized = (
            restart_result.state is PositionRestartState.RESTORE_ALLOWED
            and restart_result.can_restore
            and isinstance(position, ManagedPosition)
            and position.state is PositionState.OPEN
            and restart_result.safety_result.state is PositionSafetyState.SAFE_OPEN
            and restart_result.safety_result.recovery_result
            is restart_result.recovery_result
            and synchronization_result.managed_position is position
        )
        if not is_authorized:
            raise PositionRestorationError(
                "Restart result does not safely authorize position restoration."
            )
        if self._position_manager.active_position is not None:
            raise PositionRestorationError(
                "Runtime position manager must be flat before restoration."
            )

        self._position_manager.register(position)
        if self._position_manager.active_position is not position:
            raise PositionRestorationError(
                "Runtime position manager did not retain the restored position."
            )
        return PositionRestorationResult(
            state=PositionRestorationState.RESTORED,
            restart_result=restart_result,
            restored_position=position,
            message="Persisted position was explicitly restored into runtime state.",
        )
