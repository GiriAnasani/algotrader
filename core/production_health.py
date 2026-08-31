"""Read-only production health snapshots over an existing LIVE object graph."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import json

from trading.execution_mode import ExecutionMode
from trading.live_readiness import LiveReadinessState
from trading.live_runtime import LiveRuntimeComponents
from trading.market import MarketData
from trading.market_data_health import MarketDataHealthState


class ProductionHealthState(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    NOT_READY = "NOT_READY"


@dataclass(frozen=True)
class ProductionHealthSnapshot:
    observed_at: datetime
    state: ProductionHealthState
    readiness_state: LiveReadinessState
    market_data_state: MarketDataHealthState
    execution_enabled: bool
    pending_order: bool
    active_position: bool
    position_consistent: bool
    risk_configured: bool
    audit_configured: bool
    issues: tuple[str, ...]

    def to_dict(self):
        return {
            "observed_at": self.observed_at.isoformat(),
            "state": self.state.value,
            "readiness_state": self.readiness_state.value,
            "market_data_state": self.market_data_state.value,
            "execution_enabled": self.execution_enabled,
            "pending_order": self.pending_order,
            "active_position": self.active_position,
            "position_consistent": self.position_consistent,
            "risk_configured": self.risk_configured,
            "audit_configured": self.audit_configured,
            "issues": list(self.issues),
        }

    def to_json(self):
        return json.dumps(
            self.to_dict(), sort_keys=True, allow_nan=False,
            separators=(",", ":"),
        )


def production_position_context_consistent(position, context):
    """Return whether current position and execution-context truth agree."""
    if position is None or context is None:
        return position is None and context is None
    return (
        position.contract_symbol == context.contract_symbol
        and position.quantity == context.quantity
        and position.side.value == context.side
    )


def production_authority_identity_issues(runtime, market):
    """Return stable identity issues without observing external systems."""
    issues = []
    if runtime.position_manager is not market.live_position_manager:
        issues.append("POSITION_MANAGER_IDENTITY_MISMATCH")
    if runtime.market_data_health_tracker is not market.live_market_data_health_tracker:
        issues.append("MARKET_DATA_TRACKER_IDENTITY_MISMATCH")
    if runtime.readiness_gate is not market.live_readiness_gate:
        issues.append("READINESS_GATE_IDENTITY_MISMATCH")
    if runtime.execution_coordinator is not market.live_execution_coordinator:
        issues.append("EXECUTION_COORDINATOR_IDENTITY_MISMATCH")
    if runtime.closed_position_history is not market.live_closed_position_history:
        issues.append("CLOSED_POSITION_HISTORY_IDENTITY_MISMATCH")

    risk_pair = runtime.risk_evaluator is not None and runtime.risk_guard is not None
    if (runtime.risk_evaluator is None) is not (runtime.risk_guard is None):
        issues.append("RISK_CONFIGURATION_MISMATCH")
    elif not risk_pair:
        issues.append("RISK_NOT_CONFIGURED")
    if (
        runtime.risk_evaluator is not market.live_risk_evaluator
        or runtime.risk_guard is not market.live_risk_guard
    ):
        issues.append("RISK_IDENTITY_MISMATCH")
    if risk_pair and (
        runtime.risk_evaluator.position_manager is not runtime.position_manager
        or runtime.risk_evaluator.closed_position_history
        is not runtime.closed_position_history
    ):
        issues.append("RISK_AUTHORITY_IDENTITY_MISMATCH")

    if runtime.audit_sink is None:
        issues.append("AUDIT_NOT_CONFIGURED")
    if runtime.audit_sink is not market.live_audit_sink:
        issues.append("AUDIT_IDENTITY_MISMATCH")
    if runtime.execution_guard is not runtime.execution_coordinator._execution_guard:
        issues.append("EXECUTION_GUARD_IDENTITY_MISMATCH")
    return tuple(issues)


class ProductionHealthInspector:
    """Observes existing LIVE authorities without calls, writes, or mutation."""

    def __init__(self, runtime, market):
        if not isinstance(runtime, LiveRuntimeComponents):
            raise TypeError("Runtime must be LiveRuntimeComponents.")
        if not isinstance(market, MarketData):
            raise TypeError("Market must be MarketData.")
        if market.execution_router.mode is not ExecutionMode.LIVE:
            raise ValueError("Production health inspection requires LIVE MarketData.")
        self._runtime = runtime
        self._market = market

    def inspect(self, now):
        if not isinstance(now, datetime):
            raise TypeError("Observation time must be a datetime.")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Observation time must be timezone-aware.")

        runtime = self._runtime
        market = self._market
        issues = []
        readiness = runtime.readiness_gate.state
        market_health = runtime.market_data_health_tracker.snapshot(now).state
        enabled = runtime.execution_coordinator.enabled
        pending = market.pending_live_order is not None
        active = runtime.position_manager.active_position
        consistent = production_position_context_consistent(
            active, market.live_execution_context
        )

        if readiness is not LiveReadinessState.READY:
            issues.append("READINESS_NOT_READY")
        if market_health is not MarketDataHealthState.HEALTHY:
            issues.append(f"MARKET_DATA_{market_health.value}")
        if pending:
            issues.append("PENDING_ORDER")
        if not consistent:
            issues.append("POSITION_CONTEXT_MISMATCH")

        issues.extend(production_authority_identity_issues(runtime, market))
        risk_pair = runtime.risk_evaluator is not None and runtime.risk_guard is not None
        audit_configured = runtime.audit_sink is not None
        if not enabled:
            issues.append("EXECUTION_DISABLED")

        blocking = tuple(issue for issue in issues if issue != "EXECUTION_DISABLED")
        state = (
            ProductionHealthState.NOT_READY if blocking
            else ProductionHealthState.DEGRADED if not enabled
            else ProductionHealthState.HEALTHY
        )
        return ProductionHealthSnapshot(
            now, state, readiness, market_health, enabled, pending,
            active is not None, consistent, risk_pair, audit_configured,
            tuple(issues),
        )

    @staticmethod
    def _position_consistent(position, context):
        return production_position_context_consistent(position, context)
