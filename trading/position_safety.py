"""Pure evaluation of position safety from a recovery decision."""

from dataclasses import dataclass
from enum import Enum

from trading.position_recovery import PositionRecoveryResult, PositionRecoveryState


class PositionSafetyState(Enum):
    """Safe and blocked outcomes for further position lifecycle activity."""

    SAFE_FLAT = "SAFE_FLAT"
    SAFE_OPEN = "SAFE_OPEN"
    BLOCKED_ADOPTION_REQUIRED = "BLOCKED_ADOPTION_REQUIRED"
    BLOCKED_STALE_MANAGED_STATE = "BLOCKED_STALE_MANAGED_STATE"
    BLOCKED_AMBIGUOUS = "BLOCKED_AMBIGUOUS"


_SAFETY_STATE_BY_RECOVERY_STATE = {
    PositionRecoveryState.SAFE_FLAT: PositionSafetyState.SAFE_FLAT,
    PositionRecoveryState.SAFE_CONTINUE: PositionSafetyState.SAFE_OPEN,
    PositionRecoveryState.ADOPTION_REQUIRED: (
        PositionSafetyState.BLOCKED_ADOPTION_REQUIRED
    ),
    PositionRecoveryState.STALE_MANAGED_STATE: (
        PositionSafetyState.BLOCKED_STALE_MANAGED_STATE
    ),
    PositionRecoveryState.AMBIGUOUS: PositionSafetyState.BLOCKED_AMBIGUOUS,
}


@dataclass(frozen=True)
class PositionSafetyResult:
    """Immutable safety decision preserving the exact recovery diagnostic."""

    state: PositionSafetyState
    recovery_result: PositionRecoveryResult
    message: str

    def __post_init__(self):
        if not isinstance(self.state, PositionSafetyState):
            raise TypeError("State must be a PositionSafetyState.")
        if not isinstance(self.recovery_result, PositionRecoveryResult):
            raise TypeError("Recovery result must be a PositionRecoveryResult.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("Message must be a non-empty string.")

        expected_state = _SAFETY_STATE_BY_RECOVERY_STATE[self.recovery_result.state]
        if self.state is not expected_state:
            raise ValueError("Safety state must match the recovery state.")

        object.__setattr__(self, "message", self.message.strip())

    @property
    def is_safe(self):
        return self.state in (
            PositionSafetyState.SAFE_FLAT,
            PositionSafetyState.SAFE_OPEN,
        )


class PositionSafetyEvaluator:
    """Statelessly map one trusted recovery decision to position safety."""

    _MESSAGES = {
        PositionSafetyState.SAFE_FLAT: "Position state is safely flat.",
        PositionSafetyState.SAFE_OPEN: "Open position state is synchronized and safe.",
        PositionSafetyState.BLOCKED_ADOPTION_REQUIRED: (
            "Position activity is blocked because broker exposure requires adoption."
        ),
        PositionSafetyState.BLOCKED_STALE_MANAGED_STATE: (
            "Position activity is blocked because managed state is stale."
        ),
        PositionSafetyState.BLOCKED_AMBIGUOUS: (
            "Position activity is blocked because exposure is ambiguous."
        ),
    }

    def evaluate(self, recovery_result):
        """Return a safety decision without re-running or changing diagnostics."""
        if not isinstance(recovery_result, PositionRecoveryResult):
            raise TypeError("Recovery result must be a PositionRecoveryResult.")

        state = _SAFETY_STATE_BY_RECOVERY_STATE[recovery_result.state]
        return PositionSafetyResult(
            state=state,
            recovery_result=recovery_result,
            message=self._MESSAGES[state],
        )
