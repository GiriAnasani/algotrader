"""Explicit, construction-only assembly of LIVE MarketData."""

from trading.execution_mode import ExecutionMode
from trading.live_runtime import LiveRuntimeComponents
from trading.market import MarketData


def build_live_market_data(kite, instruments, runtime_components):
    """Constructs one LIVE MarketData using an existing runtime bundle."""
    if not isinstance(runtime_components, LiveRuntimeComponents):
        raise TypeError(
            "Runtime components must be LiveRuntimeComponents."
        )

    return MarketData(
        kite,
        instruments,
        execution_mode=ExecutionMode.LIVE,
        live_execution_coordinator=(
            runtime_components.execution_coordinator
        ),
        live_order_status_reader=runtime_components.order_status_reader,
        live_position_reader=runtime_components.position_reader,
        live_position_reconciler=runtime_components.position_reconciler,
        live_readiness_gate=runtime_components.readiness_gate,
        live_position_manager=runtime_components.position_manager,
        live_closed_position_history=runtime_components.closed_position_history,
        live_closed_position_history_store=(
            runtime_components.closed_position_history_store
        ),
        live_position_store=runtime_components.position_store,
    )
