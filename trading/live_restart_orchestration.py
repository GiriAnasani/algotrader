"""Explicit orchestration of durable continuity after LIVE startup."""

from dataclasses import dataclass
from enum import Enum

from trading.live_continuity_readiness import LiveContinuityReadinessCoordinator
from trading.live_readiness import LiveReadinessGate
from trading.live_recovery import LiveRecoveryResult, LiveRecoveryState
from trading.live_runtime import LiveRuntimeComponents
from trading.live_startup import LiveStartupResult
from trading.position_continuity import (
    PositionContinuityResult,
    PositionContinuityState,
    PositionContinuityVerifier,
)
from trading.position_manager import PositionManager
from trading.position_restart import (
    PositionRestartCoordinator,
    PositionRestartResult,
    PositionRestartState,
)
from trading.position_restorer import (
    PositionRestorationResult,
    PositionRestorationState,
    PositionRestorer,
)
from trading.position_store import PositionStore


class LiveRestartOrchestrationError(RuntimeError):
    """Raised when the supplied LIVE startup graph is inconsistent."""


class LiveRestartOrchestrationState(Enum):
    """Outcomes of explicit LIVE restart continuity orchestration."""

    READY_FLAT = "READY_FLAT"
    READY_RESTORED = "READY_RESTORED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class LiveRestartOrchestrationResult:
    """Immutable diagnostic chain from startup through continuity readiness."""

    state: LiveRestartOrchestrationState
    startup_result: LiveStartupResult
    restart_result: PositionRestartResult | None
    restoration_result: PositionRestorationResult | None
    continuity_result: PositionContinuityResult | None
    message: str

    def __post_init__(self):
        if not isinstance(self.state, LiveRestartOrchestrationState):
            raise TypeError("State must be a LiveRestartOrchestrationState.")
        if not isinstance(self.startup_result, LiveStartupResult):
            raise TypeError("Startup result must be a LiveStartupResult.")
        if self.restart_result is not None and not isinstance(
            self.restart_result, PositionRestartResult
        ):
            raise TypeError("Restart result must be a PositionRestartResult or None.")
        if self.restoration_result is not None and not isinstance(
            self.restoration_result, PositionRestorationResult
        ):
            raise TypeError(
                "Restoration result must be a PositionRestorationResult or None."
            )
        if self.continuity_result is not None and not isinstance(
            self.continuity_result, PositionContinuityResult
        ):
            raise TypeError(
                "Continuity result must be a PositionContinuityResult or None."
            )
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("Message must be a non-empty string.")

        if self.state is LiveRestartOrchestrationState.READY_FLAT:
            self._validate_ready_flat()
        elif self.state is LiveRestartOrchestrationState.READY_RESTORED:
            self._validate_ready_restored()
        else:
            self._validate_blocked()
        object.__setattr__(self, "message", self.message.strip())

    @property
    def is_ready(self):
        return self.state in (
            LiveRestartOrchestrationState.READY_FLAT,
            LiveRestartOrchestrationState.READY_RESTORED,
        )

    def _validate_ready_flat(self):
        if self.restart_result is None or (
            self.restart_result.state is not PositionRestartState.SAFE_FLAT
        ):
            raise ValueError("READY_FLAT requires a SAFE_FLAT restart result.")
        if self.restoration_result is not None:
            raise ValueError("READY_FLAT cannot contain a restoration result.")
        if self.continuity_result is None or (
            self.continuity_result.state
            is not PositionContinuityState.VERIFIED_FLAT
        ):
            raise ValueError("READY_FLAT requires VERIFIED_FLAT continuity.")
        if self.continuity_result.restart_result is not self.restart_result:
            raise ValueError("Continuity must retain the exact restart result.")
        if not self.startup_result.runtime.readiness_gate.is_ready:
            raise ValueError("READY_FLAT requires a READY gate.")
        if self.startup_result.runtime.position_manager.active_position is not None:
            raise ValueError("READY_FLAT requires a flat runtime manager.")

    def _validate_ready_restored(self):
        restart = self.restart_result
        restoration = self.restoration_result
        continuity = self.continuity_result
        if restart is None or restart.state is not PositionRestartState.RESTORE_ALLOWED:
            raise ValueError(
                "READY_RESTORED requires a RESTORE_ALLOWED restart result."
            )
        if restoration is None or (
            restoration.state is not PositionRestorationState.RESTORED
        ):
            raise ValueError("READY_RESTORED requires a RESTORED result.")
        if continuity is None or (
            continuity.state is not PositionContinuityState.VERIFIED_RESTORED
        ):
            raise ValueError(
                "READY_RESTORED requires VERIFIED_RESTORED continuity."
            )
        position = restart.persisted_position
        if restoration.restart_result is not restart:
            raise ValueError("Restoration must retain the exact restart result.")
        if continuity.restart_result is not restart:
            raise ValueError("Continuity must retain the exact restart result.")
        if continuity.restoration_result is not restoration:
            raise ValueError("Continuity must retain the exact restoration result.")
        if not (
            restoration.restored_position
            is continuity.runtime_position
            is position
            is self.startup_result.runtime.position_manager.active_position
        ):
            raise ValueError("Restored runtime position identities must match.")
        if self.startup_result.market.live_position_manager is not (
            self.startup_result.runtime.position_manager
        ):
            raise ValueError("MarketData must retain the runtime position manager.")
        if not self.startup_result.runtime.readiness_gate.is_ready:
            raise ValueError("READY_RESTORED requires a READY gate.")

    def _validate_blocked(self):
        if self.startup_result.runtime.readiness_gate.is_ready:
            raise ValueError("BLOCKED requires a NOT_READY gate.")
        if self.restoration_result is not None or self.continuity_result is not None:
            raise ValueError("BLOCKED cannot claim restoration or continuity.")
        if self.restart_result is not None and self.restart_result.state in (
            PositionRestartState.SAFE_FLAT,
            PositionRestartState.RESTORE_ALLOWED,
        ):
            raise ValueError("BLOCKED cannot contain a safe restart result.")


class LiveRestartOrchestrator:
    """Compose existing restart primitives using startup's broker snapshot."""

    def __init__(self, position_store):
        if not isinstance(position_store, PositionStore):
            raise TypeError("Position store must be a PositionStore.")
        self._position_store = position_store

    def orchestrate(self, startup_result):
        """Explicitly establish durable continuity or return a blocked result."""
        if not isinstance(startup_result, LiveStartupResult):
            raise TypeError("Startup result must be a LiveStartupResult.")
        runtime = startup_result.runtime
        if not isinstance(runtime, LiveRuntimeComponents):
            raise TypeError("Startup runtime must be LiveRuntimeComponents.")
        manager = getattr(runtime, "position_manager", None)
        gate = getattr(runtime, "readiness_gate", None)
        if not isinstance(manager, PositionManager):
            raise TypeError("Runtime position manager must be a PositionManager.")
        if not isinstance(gate, LiveReadinessGate):
            raise TypeError("Runtime readiness gate must be a LiveReadinessGate.")
        if getattr(startup_result.market, "live_position_manager", None) is not manager:
            raise LiveRestartOrchestrationError(
                "MarketData and runtime must share the exact position manager."
            )
        recovery = startup_result.recovery_result
        if not isinstance(recovery, LiveRecoveryResult):
            raise TypeError("Startup recovery result must be a LiveRecoveryResult.")

        gate.revoke()
        if recovery.state not in (
            LiveRecoveryState.SAFE_FLAT,
            LiveRecoveryState.POSITION_PRESENT,
        ):
            return self._blocked(startup_result, None, "Startup recovery is unresolved.")

        restart = PositionRestartCoordinator(self._position_store).recover(
            recovery.broker_positions
        )
        if restart.state is PositionRestartState.SAFE_FLAT:
            try:
                continuity = PositionContinuityVerifier(manager).verify(restart)
                LiveContinuityReadinessCoordinator(gate).apply(continuity)
                return LiveRestartOrchestrationResult(
                    state=LiveRestartOrchestrationState.READY_FLAT,
                    startup_result=startup_result,
                    restart_result=restart,
                    restoration_result=None,
                    continuity_result=continuity,
                    message="LIVE restart continuity is ready and flat.",
                )
            except Exception:
                gate.revoke()
                raise

        if restart.state is PositionRestartState.RESTORE_ALLOWED:
            restoration = None
            try:
                restoration = PositionRestorer(manager).restore(restart)
                continuity = PositionContinuityVerifier(manager).verify(
                    restart, restoration
                )
                LiveContinuityReadinessCoordinator(gate).apply(continuity)
                return LiveRestartOrchestrationResult(
                    state=LiveRestartOrchestrationState.READY_RESTORED,
                    startup_result=startup_result,
                    restart_result=restart,
                    restoration_result=restoration,
                    continuity_result=continuity,
                    message="LIVE restart continuity is ready with restored state.",
                )
            except Exception:
                gate.revoke()
                raise

        return self._blocked(
            startup_result,
            restart,
            "Durable and broker position continuity requires review.",
        )

    @staticmethod
    def _blocked(startup_result, restart_result, message):
        return LiveRestartOrchestrationResult(
            state=LiveRestartOrchestrationState.BLOCKED,
            startup_result=startup_result,
            restart_result=restart_result,
            restoration_result=None,
            continuity_result=None,
            message=message,
        )
