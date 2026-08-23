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
):
    """Builds LIVE dependencies and performs one explicit recovery inspection."""
    runtime = build_live_runtime(
        kite_client,
        execution_enabled=execution_enabled,
    )
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
