"""Pure authorization decision for a recovered LIVE session."""

from dataclasses import dataclass
from enum import Enum

from trading.live_startup import LiveStartupResult


class LiveRunAuthorizationState(Enum):
    """Whether current LIVE prerequisites permit a future runtime start."""

    BLOCKED = "BLOCKED"
    PERMITTED = "PERMITTED"


@dataclass(frozen=True)
class LiveRunAuthorizationResult:
    """Immutable snapshot of one LIVE run-authorization decision."""

    state: LiveRunAuthorizationState
    readiness_ready: bool
    execution_enabled: bool
    message: str

    def __post_init__(self):
        if not isinstance(self.state, LiveRunAuthorizationState):
            raise TypeError("State must be a LiveRunAuthorizationState.")
        if not isinstance(self.readiness_ready, bool):
            raise TypeError("Readiness ready must be a boolean.")
        if not isinstance(self.execution_enabled, bool):
            raise TypeError("Execution enabled must be a boolean.")
        if not isinstance(self.message, str) or not self.message:
            raise ValueError("Message must be a non-empty string.")

    @property
    def is_permitted(self):
        return self.state is LiveRunAuthorizationState.PERMITTED


class LiveRunAuthorizer:
    """Observes current LIVE state without mutating or starting anything."""

    def authorize(self, startup_result):
        if not isinstance(startup_result, LiveStartupResult):
            raise TypeError("Startup result must be a LiveStartupResult.")

        readiness_ready = startup_result.runtime.readiness_gate.is_ready
        execution_enabled = (
            startup_result.runtime.execution_coordinator.enabled
        )

        if readiness_ready and execution_enabled:
            state = LiveRunAuthorizationState.PERMITTED
            message = "LIVE runtime start is permitted."
        elif not readiness_ready and not execution_enabled:
            state = LiveRunAuthorizationState.BLOCKED
            message = (
                "LIVE runtime start is blocked because readiness is "
                "NOT_READY and execution is disabled."
            )
        elif not readiness_ready:
            state = LiveRunAuthorizationState.BLOCKED
            message = (
                "LIVE runtime start is blocked because readiness is NOT_READY."
            )
        else:
            state = LiveRunAuthorizationState.BLOCKED
            message = (
                "LIVE runtime start is blocked because execution is disabled."
            )

        return LiveRunAuthorizationResult(
            state=state,
            readiness_ready=readiness_ready,
            execution_enabled=execution_enabled,
            message=message,
        )
