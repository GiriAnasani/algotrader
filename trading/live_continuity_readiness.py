"""Explicit application of verified position continuity to LIVE readiness."""

from trading.live_readiness import LiveReadinessGate
from trading.position_continuity import PositionContinuityResult


class LiveContinuityReadinessError(RuntimeError):
    """Raised when verified continuity cannot establish LIVE readiness."""


class LiveContinuityReadinessCoordinator:
    """Apply one trusted continuity proof to the existing readiness gate."""

    def __init__(self, readiness_gate):
        if not isinstance(readiness_gate, LiveReadinessGate):
            raise TypeError("Readiness gate must be a LiveReadinessGate.")
        self._readiness_gate = readiness_gate

    def apply(self, continuity_result):
        """Replace readiness using one explicitly supplied continuity proof."""
        self._readiness_gate.revoke()
        if not isinstance(continuity_result, PositionContinuityResult):
            raise TypeError("Continuity result must be a PositionContinuityResult.")

        self._readiness_gate.apply_continuity_result(continuity_result)
        if not self._readiness_gate.is_ready:
            raise LiveContinuityReadinessError(
                "Verified continuity did not establish LIVE readiness."
            )
        return continuity_result
