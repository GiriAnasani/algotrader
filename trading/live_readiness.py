"""Fail-closed readiness state for explicit LIVE recovery results."""

from enum import Enum

from trading.position_continuity import PositionContinuityResult
from trading.live_recovery import LiveRecoveryResult, LiveRecoveryState


class LiveReadinessState(Enum):
    """Whether LIVE execution has been explicitly proven ready."""

    NOT_READY = "NOT_READY"
    READY = "READY"


class LiveReadinessGate:
    """Tracks readiness established by the latest valid recovery result."""

    def __init__(self):
        self._state = LiveReadinessState.NOT_READY
        self._last_recovery_result = None
        self._last_continuity_result = None

    @property
    def state(self):
        return self._state

    @property
    def is_ready(self):
        return self._state is LiveReadinessState.READY

    @property
    def last_recovery_result(self):
        return self._last_recovery_result

    @property
    def last_continuity_result(self):
        return self._last_continuity_result

    def apply_recovery_result(self, recovery_result):
        """Replace readiness according to one explicitly supplied result."""
        if not isinstance(recovery_result, LiveRecoveryResult):
            raise TypeError("Recovery result must be a LiveRecoveryResult.")

        self._last_recovery_result = recovery_result
        if recovery_result.state is LiveRecoveryState.SAFE_FLAT:
            self._state = LiveReadinessState.READY
        else:
            self._state = LiveReadinessState.NOT_READY

    def apply_continuity_result(self, continuity_result):
        """Establish readiness from one explicitly verified continuity result."""
        if not isinstance(continuity_result, PositionContinuityResult):
            raise TypeError("Continuity result must be a PositionContinuityResult.")

        self._last_continuity_result = continuity_result
        self._state = LiveReadinessState.READY

    def revoke(self):
        """Fail closed while retaining the last result for diagnostics."""
        self._state = LiveReadinessState.NOT_READY
