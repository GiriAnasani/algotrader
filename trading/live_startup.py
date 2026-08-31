"""Explicit LIVE construction and recovery orchestration without runtime start."""

from dataclasses import dataclass

from trading.live_market_factory import build_live_market_data
from trading.live_recovery import LiveRecoveryResult
from trading.live_runtime import LiveRuntimeComponents, build_live_runtime
from trading.market import MarketData


@dataclass(frozen=True)
class LiveStartupResult:
    """Immutable objects and diagnostics produced by explicit LIVE startup."""

    runtime: LiveRuntimeComponents
    market: MarketData
    recovery_result: LiveRecoveryResult

    @property
    def is_ready(self):
        return self.runtime.readiness_gate.is_ready


def initialize_live_session(
    kite_client,
    instruments,
    execution_enabled=False,
    allowed_contract_symbols=None,
    closed_position_history_store=None,
):
    """Builds LIVE dependencies and performs one explicit recovery inspection."""
    if execution_enabled is not False:
        if not isinstance(execution_enabled, bool):
            raise TypeError("Execution enabled must be a boolean.")
        raise ValueError(
            "initialize_live_session cannot enable execution; use the "
            "production application composition boundary."
        )
    runtime_arguments = {"execution_enabled": execution_enabled}
    if closed_position_history_store is not None:
        runtime_arguments["closed_position_history_store"] = (
            closed_position_history_store
        )
    runtime = build_live_runtime(kite_client, **runtime_arguments)
    market = build_live_market_data(
        kite_client,
        instruments,
        runtime,
    )
    recovery_result = runtime.session_bootstrap.initialize(
        allowed_contract_symbols=allowed_contract_symbols,
    )
    return LiveStartupResult(
        runtime=runtime,
        market=market,
        recovery_result=recovery_result,
    )
