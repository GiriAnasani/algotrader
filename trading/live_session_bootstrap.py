"""Explicit orchestration of LIVE recovery and session readiness."""

from trading.live_readiness import LiveReadinessGate
from trading.live_recovery import LiveRecoveryCoordinator


class LiveSessionBootstrap:
    """Refreshes LIVE readiness from one explicit broker recovery result."""

    def __init__(self, recovery_coordinator, readiness_gate):
        if not isinstance(recovery_coordinator, LiveRecoveryCoordinator):
            raise TypeError(
                "Recovery coordinator must be a LiveRecoveryCoordinator."
            )
        if not isinstance(readiness_gate, LiveReadinessGate):
            raise TypeError("Readiness gate must be a LiveReadinessGate.")

        self._recovery_coordinator = recovery_coordinator
        self._readiness_gate = readiness_gate

    def initialize(self, allowed_contract_symbols=None):
        """Replaces prior authorization with one fresh recovery inspection."""
        self._readiness_gate.revoke()
        result = self._recovery_coordinator.recover(
            allowed_contract_symbols=allowed_contract_symbols
        )
        self._readiness_gate.apply_recovery_result(result)
        return result
