"""Side-effect-free composition of guarded LIVE runtime dependencies."""

from dataclasses import dataclass

from trading.broker_position_reconciler import BrokerPositionReconciler
from trading.closed_position_history import ClosedPositionHistory
from trading.closed_position_history_store import ClosedPositionHistoryStore
from trading.live_execution import LiveExecutionCoordinator
from trading.execution_guard import LiveExecutionGuard
from trading.market_data_health import MarketDataHealthTracker
from trading.live_readiness import LiveReadinessGate
from trading.live_recovery import LiveRecoveryCoordinator
from trading.live_session_bootstrap import LiveSessionBootstrap
from trading.position_manager import PositionManager
from trading.position_store import PositionStore
from trading.zerodha_order_adapter import ZerodhaOrderAdapter
from trading.zerodha_order_list_reader import ZerodhaOrderListReader
from trading.zerodha_order_status_reader import ZerodhaOrderStatusReader
from trading.zerodha_order_submitter import ZerodhaOrderSubmitter
from trading.zerodha_position_reader import ZerodhaPositionReader


@dataclass(frozen=True)
class LiveRuntimeComponents:
    """Immutable references to one consistently wired LIVE dependency graph."""

    readiness_gate: LiveReadinessGate
    position_reader: ZerodhaPositionReader
    order_list_reader: ZerodhaOrderListReader
    recovery_coordinator: LiveRecoveryCoordinator
    session_bootstrap: LiveSessionBootstrap
    order_status_reader: ZerodhaOrderStatusReader
    order_adapter: ZerodhaOrderAdapter
    order_submitter: ZerodhaOrderSubmitter
    execution_coordinator: LiveExecutionCoordinator
    position_reconciler: BrokerPositionReconciler
    position_manager: PositionManager
    closed_position_history: ClosedPositionHistory
    closed_position_history_store: ClosedPositionHistoryStore | None
    position_store: PositionStore | None
    market_data_health_tracker: MarketDataHealthTracker
    execution_guard: LiveExecutionGuard


def build_live_runtime(
    kite_client,
    execution_enabled=False,
    position_store=None,
    closed_position_history_store=None,
):
    """Builds guarded LIVE dependencies without broker or recovery activity."""
    if kite_client is None:
        raise ValueError("A Kite-compatible client is required.")
    if not isinstance(execution_enabled, bool):
        raise TypeError("Execution enabled must be a boolean.")
    if position_store is not None and not isinstance(position_store, PositionStore):
        raise TypeError("Position store must be a PositionStore or None.")
    if closed_position_history_store is not None and not isinstance(
        closed_position_history_store, ClosedPositionHistoryStore
    ):
        raise TypeError(
            "Closed-position history store must be a ClosedPositionHistoryStore or None."
        )

    readiness_gate = LiveReadinessGate()
    position_manager = PositionManager()
    closed_position_history = ClosedPositionHistory()
    market_data_health_tracker = MarketDataHealthTracker()
    execution_guard = LiveExecutionGuard()
    position_reader = ZerodhaPositionReader(kite_client)
    order_list_reader = ZerodhaOrderListReader(kite_client)
    position_reconciler = BrokerPositionReconciler()
    recovery_coordinator = LiveRecoveryCoordinator(
        position_reader,
        order_list_reader,
        position_reconciler,
    )
    session_bootstrap = LiveSessionBootstrap(
        recovery_coordinator,
        readiness_gate,
    )
    order_status_reader = ZerodhaOrderStatusReader(kite_client)
    order_adapter = ZerodhaOrderAdapter()
    order_submitter = ZerodhaOrderSubmitter(kite_client)
    execution_coordinator = LiveExecutionCoordinator(
        order_adapter,
        order_submitter,
        enabled=execution_enabled,
        execution_guard=execution_guard,
    )

    return LiveRuntimeComponents(
        readiness_gate=readiness_gate,
        position_reader=position_reader,
        order_list_reader=order_list_reader,
        recovery_coordinator=recovery_coordinator,
        session_bootstrap=session_bootstrap,
        order_status_reader=order_status_reader,
        order_adapter=order_adapter,
        order_submitter=order_submitter,
        execution_coordinator=execution_coordinator,
        position_reconciler=position_reconciler,
        position_manager=position_manager,
        closed_position_history=closed_position_history,
        closed_position_history_store=closed_position_history_store,
        position_store=position_store,
        market_data_health_tracker=market_data_health_tracker,
        execution_guard=execution_guard,
    )
