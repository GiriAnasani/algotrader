"""Decision-only restart continuity for managed position state."""

from dataclasses import dataclass
from enum import Enum

from trading.position_manager import PositionManager
from trading.position_synchronizer import (
    PositionSynchronizationResult,
    PositionSynchronizationState,
    PositionSynchronizer,
)


class PositionRecoveryState(Enum):
    """Conservative restart decisions derived from synchronization state."""

    SAFE_FLAT = "SAFE_FLAT"
    SAFE_CONTINUE = "SAFE_CONTINUE"
    ADOPTION_REQUIRED = "ADOPTION_REQUIRED"
    STALE_MANAGED_STATE = "STALE_MANAGED_STATE"
    AMBIGUOUS = "AMBIGUOUS"


_RECOVERY_STATE_BY_SYNCHRONIZATION_STATE = {
    PositionSynchronizationState.SYNCHRONIZED_FLAT: PositionRecoveryState.SAFE_FLAT,
    PositionSynchronizationState.SYNCHRONIZED_OPEN: PositionRecoveryState.SAFE_CONTINUE,
    PositionSynchronizationState.BROKER_ONLY: PositionRecoveryState.ADOPTION_REQUIRED,
    PositionSynchronizationState.MANAGED_ONLY: PositionRecoveryState.STALE_MANAGED_STATE,
    PositionSynchronizationState.MISMATCH: PositionRecoveryState.AMBIGUOUS,
}


@dataclass(frozen=True)
class PositionRecoveryResult:
    """Immutable recovery decision preserving its synchronization diagnostic."""

    state: PositionRecoveryState
    synchronization_result: PositionSynchronizationResult
    message: str

    def __post_init__(self):
        if not isinstance(self.state, PositionRecoveryState):
            raise TypeError("State must be a PositionRecoveryState.")
        if not isinstance(self.synchronization_result, PositionSynchronizationResult):
            raise TypeError(
                "Synchronization result must be a PositionSynchronizationResult."
            )
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("Message must be a non-empty string.")

        expected_state = _RECOVERY_STATE_BY_SYNCHRONIZATION_STATE[
            self.synchronization_result.state
        ]
        if self.state is not expected_state:
            raise ValueError("Recovery state must match the synchronization state.")

        object.__setattr__(self, "message", self.message.strip())


class PositionRecoveryCoordinator:
    """Derive restart continuity decisions from an already-read snapshot."""

    _MESSAGES = {
        PositionRecoveryState.SAFE_FLAT: "Managed and broker state are safely flat.",
        PositionRecoveryState.SAFE_CONTINUE: (
            "Managed and broker exposure agree for continuity."
        ),
        PositionRecoveryState.ADOPTION_REQUIRED: (
            "Broker exposure exists without managed state; adoption requires review."
        ),
        PositionRecoveryState.STALE_MANAGED_STATE: (
            "Managed exposure is absent from broker truth and requires review."
        ),
        PositionRecoveryState.AMBIGUOUS: (
            "Managed and broker state are ambiguous and require review."
        ),
    }

    def __init__(self, position_manager):
        if not isinstance(position_manager, PositionManager):
            raise TypeError("Position manager must be a PositionManager.")
        self._position_synchronizer = PositionSynchronizer(position_manager)

    def recover(self, broker_positions):
        """Return a recovery decision without changing managed or broker state."""
        synchronization_result = self._position_synchronizer.synchronize(
            broker_positions
        )
        state = _RECOVERY_STATE_BY_SYNCHRONIZATION_STATE[
            synchronization_result.state
        ]
        return PositionRecoveryResult(
            state=state,
            synchronization_result=synchronization_result,
            message=self._MESSAGES[state],
        )
