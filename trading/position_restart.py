"""Read-only orchestration of durable position restart diagnostics."""

from dataclasses import dataclass
from enum import Enum

from trading.position import ManagedPosition, PositionState
from trading.position_manager import PositionManager
from trading.position_recovery import (
    PositionRecoveryCoordinator,
    PositionRecoveryResult,
)
from trading.position_safety import (
    PositionSafetyEvaluator,
    PositionSafetyResult,
    PositionSafetyState,
)
from trading.position_store import PositionStore


class PositionRestartState(Enum):
    """Restart decisions derived from durable and current broker state."""

    SAFE_FLAT = "SAFE_FLAT"
    RESTORE_ALLOWED = "RESTORE_ALLOWED"
    ADOPTION_REQUIRED = "ADOPTION_REQUIRED"
    STALE_PERSISTED_STATE = "STALE_PERSISTED_STATE"
    AMBIGUOUS = "AMBIGUOUS"


_RESTART_STATE_BY_SAFETY_STATE = {
    PositionSafetyState.SAFE_FLAT: PositionRestartState.SAFE_FLAT,
    PositionSafetyState.SAFE_OPEN: PositionRestartState.RESTORE_ALLOWED,
    PositionSafetyState.BLOCKED_ADOPTION_REQUIRED: (
        PositionRestartState.ADOPTION_REQUIRED
    ),
    PositionSafetyState.BLOCKED_STALE_MANAGED_STATE: (
        PositionRestartState.STALE_PERSISTED_STATE
    ),
    PositionSafetyState.BLOCKED_AMBIGUOUS: PositionRestartState.AMBIGUOUS,
}


@dataclass(frozen=True)
class PositionRestartResult:
    """Immutable restart decision retaining the exact diagnostic chain."""

    state: PositionRestartState
    persisted_position: ManagedPosition | None
    recovery_result: PositionRecoveryResult
    safety_result: PositionSafetyResult
    message: str

    def __post_init__(self):
        if not isinstance(self.state, PositionRestartState):
            raise TypeError("State must be a PositionRestartState.")
        if self.persisted_position is not None and not isinstance(
            self.persisted_position, ManagedPosition
        ):
            raise TypeError("Persisted position must be a ManagedPosition or None.")
        if not isinstance(self.recovery_result, PositionRecoveryResult):
            raise TypeError("Recovery result must be a PositionRecoveryResult.")
        if not isinstance(self.safety_result, PositionSafetyResult):
            raise TypeError("Safety result must be a PositionSafetyResult.")
        if self.safety_result.recovery_result is not self.recovery_result:
            raise ValueError("Safety result must contain the exact recovery result.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("Message must be a non-empty string.")

        expected_state = _RESTART_STATE_BY_SAFETY_STATE[self.safety_result.state]
        if self.state is not expected_state:
            raise ValueError("Restart state must match the safety state.")

        synchronized_position = (
            self.recovery_result.synchronization_result.managed_position
        )
        if self.state is PositionRestartState.SAFE_FLAT:
            if self.persisted_position is not None:
                raise ValueError("SAFE_FLAT requires absent persisted state.")
        elif self.state is PositionRestartState.RESTORE_ALLOWED:
            if self.persisted_position is None:
                raise ValueError("RESTORE_ALLOWED requires a persisted position.")
            if self.persisted_position.state is not PositionState.OPEN:
                raise ValueError("RESTORE_ALLOWED requires an OPEN persisted position.")
            if synchronized_position is not self.persisted_position:
                raise ValueError(
                    "RESTORE_ALLOWED requires the exact persisted position diagnostic."
                )
        elif self.state is PositionRestartState.ADOPTION_REQUIRED:
            if self.persisted_position is not None:
                raise ValueError("ADOPTION_REQUIRED requires absent persisted state.")
        elif self.state is PositionRestartState.STALE_PERSISTED_STATE:
            if self.persisted_position is None:
                raise ValueError(
                    "STALE_PERSISTED_STATE requires a persisted position."
                )

        object.__setattr__(self, "message", self.message.strip())

    @property
    def is_safe(self):
        return self.state in (
            PositionRestartState.SAFE_FLAT,
            PositionRestartState.RESTORE_ALLOWED,
        )

    @property
    def can_restore(self):
        return self.state is PositionRestartState.RESTORE_ALLOWED


class PositionRestartCoordinator:
    """Compare stored continuity with one already-read broker snapshot."""

    _MESSAGES = {
        PositionRestartState.SAFE_FLAT: (
            "Persisted and broker position state are safely flat."
        ),
        PositionRestartState.RESTORE_ALLOWED: (
            "Persisted position agrees with broker truth and may be restored explicitly."
        ),
        PositionRestartState.ADOPTION_REQUIRED: (
            "Broker exposure has no persisted owner and requires explicit adoption."
        ),
        PositionRestartState.STALE_PERSISTED_STATE: (
            "Persisted position is absent from broker truth and requires review."
        ),
        PositionRestartState.AMBIGUOUS: (
            "Persisted and broker position state conflict and require review."
        ),
    }

    def __init__(self, position_store):
        if not isinstance(position_store, PositionStore):
            raise TypeError("Position store must be a PositionStore.")
        self._position_store = position_store

    def recover(self, broker_positions):
        """Return diagnostics without changing stored or runtime position state."""
        persisted_position = self._position_store.load()
        temporary_manager = PositionManager()
        if persisted_position is not None:
            temporary_manager.register(persisted_position)

        recovery_result = PositionRecoveryCoordinator(temporary_manager).recover(
            broker_positions
        )
        safety_result = PositionSafetyEvaluator().evaluate(recovery_result)
        state = _RESTART_STATE_BY_SAFETY_STATE[safety_result.state]
        return PositionRestartResult(
            state=state,
            persisted_position=persisted_position,
            recovery_result=recovery_result,
            safety_result=safety_result,
            message=self._MESSAGES[state],
        )
