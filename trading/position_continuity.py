"""Read-only verification of runtime position continuity."""

from dataclasses import dataclass
from enum import Enum

from trading.position import ManagedPosition, PositionState
from trading.position_manager import PositionManager
from trading.position_restart import PositionRestartResult, PositionRestartState
from trading.position_restorer import (
    PositionRestorationResult,
    PositionRestorationState,
)


class PositionContinuityError(RuntimeError):
    """Raised when runtime position continuity cannot be proven."""


class PositionContinuityState(Enum):
    """Successfully verified runtime continuity outcomes."""

    VERIFIED_FLAT = "VERIFIED_FLAT"
    VERIFIED_RESTORED = "VERIFIED_RESTORED"


@dataclass(frozen=True)
class PositionContinuityResult:
    """Immutable proof that runtime state matches the restart decision."""

    state: PositionContinuityState
    restart_result: PositionRestartResult
    restoration_result: PositionRestorationResult | None
    runtime_position: ManagedPosition | None
    message: str

    def __post_init__(self):
        if not isinstance(self.state, PositionContinuityState):
            raise TypeError("State must be a PositionContinuityState.")
        if not isinstance(self.restart_result, PositionRestartResult):
            raise TypeError("Restart result must be a PositionRestartResult.")
        if self.restoration_result is not None and not isinstance(
            self.restoration_result, PositionRestorationResult
        ):
            raise TypeError(
                "Restoration result must be a PositionRestorationResult or None."
            )
        if self.runtime_position is not None and not isinstance(
            self.runtime_position, ManagedPosition
        ):
            raise TypeError("Runtime position must be a ManagedPosition or None.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("Message must be a non-empty string.")

        if self.state is PositionContinuityState.VERIFIED_FLAT:
            self._validate_flat()
        elif self.state is PositionContinuityState.VERIFIED_RESTORED:
            self._validate_restored()

        object.__setattr__(self, "message", self.message.strip())

    def _validate_flat(self):
        if self.restart_result.state is not PositionRestartState.SAFE_FLAT:
            raise ValueError("VERIFIED_FLAT requires a SAFE_FLAT restart result.")
        if self.restart_result.persisted_position is not None:
            raise ValueError("VERIFIED_FLAT requires absent durable position state.")
        if self.restart_result.can_restore:
            raise ValueError("VERIFIED_FLAT cannot authorize restoration.")
        if self.restoration_result is not None:
            raise ValueError("VERIFIED_FLAT cannot contain a restoration result.")
        if self.runtime_position is not None:
            raise ValueError("VERIFIED_FLAT requires flat runtime state.")

    def _validate_restored(self):
        restart = self.restart_result
        restoration = self.restoration_result
        position = restart.persisted_position
        if restart.state is not PositionRestartState.RESTORE_ALLOWED:
            raise ValueError(
                "VERIFIED_RESTORED requires a RESTORE_ALLOWED restart result."
            )
        if not restart.can_restore:
            raise ValueError("VERIFIED_RESTORED requires restoration authorization.")
        if not isinstance(position, ManagedPosition) or position.state is not PositionState.OPEN:
            raise ValueError("VERIFIED_RESTORED requires an OPEN durable position.")
        if restoration is None:
            raise ValueError("VERIFIED_RESTORED requires a restoration result.")
        if restoration.state is not PositionRestorationState.RESTORED:
            raise ValueError("Restoration result must have RESTORED state.")
        if restoration.restart_result is not restart:
            raise ValueError("Restoration must belong to the exact restart result.")
        if restoration.restored_position is not position:
            raise ValueError("Restoration must contain the exact authorized position.")
        if self.runtime_position is not position:
            raise ValueError("Runtime state must contain the exact authorized position.")
        if self.runtime_position is not restoration.restored_position:
            raise ValueError("Runtime and restored position identities must match.")


class PositionContinuityVerifier:
    """Prove runtime continuity without changing manager state."""

    def __init__(self, position_manager):
        if not isinstance(position_manager, PositionManager):
            raise TypeError("Position manager must be a PositionManager.")
        self._position_manager = position_manager

    def verify(self, restart_result, restoration_result=None):
        """Verify flat or restored continuity using exact object identities."""
        if not isinstance(restart_result, PositionRestartResult):
            raise TypeError("Restart result must be a PositionRestartResult.")
        if restoration_result is not None and not isinstance(
            restoration_result, PositionRestorationResult
        ):
            raise TypeError(
                "Restoration result must be a PositionRestorationResult or None."
            )

        runtime_position = self._position_manager.active_position
        if restart_result.state is PositionRestartState.SAFE_FLAT:
            if (
                restart_result.persisted_position is not None
                or restart_result.can_restore
                or restoration_result is not None
                or runtime_position is not None
            ):
                raise PositionContinuityError(
                    "Safe-flat runtime continuity cannot be proven."
                )
            return PositionContinuityResult(
                state=PositionContinuityState.VERIFIED_FLAT,
                restart_result=restart_result,
                restoration_result=None,
                runtime_position=None,
                message="Runtime position continuity is verified flat.",
            )

        if restart_result.state is PositionRestartState.RESTORE_ALLOWED:
            position = restart_result.persisted_position
            is_verified = (
                restart_result.can_restore
                and isinstance(position, ManagedPosition)
                and position.state is PositionState.OPEN
                and restoration_result is not None
                and restoration_result.state is PositionRestorationState.RESTORED
                and restoration_result.restart_result is restart_result
                and restoration_result.restored_position is position
                and runtime_position is position
                and runtime_position is restoration_result.restored_position
            )
            if not is_verified:
                raise PositionContinuityError(
                    "Restored runtime continuity cannot be proven."
                )
            return PositionContinuityResult(
                state=PositionContinuityState.VERIFIED_RESTORED,
                restart_result=restart_result,
                restoration_result=restoration_result,
                runtime_position=runtime_position,
                message="Restored runtime position continuity is verified.",
            )

        raise PositionContinuityError(
            "Blocked restart state cannot establish runtime continuity."
        )
