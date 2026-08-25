from datetime import datetime, timedelta

import pytest

from trading.live_market_factory import build_live_market_data
from trading.live_recovery import LiveRecoveryResult, LiveRecoveryState
from trading.live_risk import LiveRiskLimits, LiveRiskViolationError
from trading.live_runtime import build_live_runtime
from trading.option_charges import (
    NetPnLCalculator,
    OptionChargeSchedule,
    OptionTradeChargesCalculator,
)
from trading.realized_net_pnl import RealizedNetPnLAggregator
from trading.session_net_pnl import SessionNetPnLAggregator
from trading.market_data_health import MarketDataHealthState
from trading.ohlc import EXCHANGE_TIMEZONE
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.market import LiveExecutionContext
from trading.strategy import SignalAction, StrategyResult


NOW = datetime(2026, 8, 25, 9, 15, tzinfo=EXCHANGE_TIMEZONE)


def risk_pnl():
    return SessionNetPnLAggregator(
        RealizedNetPnLAggregator(
            NetPnLCalculator(
                OptionTradeChargesCalculator(
                    OptionChargeSchedule(0, 0, 0, 0, 0, 0)
                )
            )
        )
    )


class FakeClient:
    def __init__(self):
        self.positions_calls = self.orders_calls = 0
        self.order_history_calls = self.place_order_calls = 0
        self.modify_order_calls = self.cancel_order_calls = 0

    def positions(self):
        self.positions_calls += 1
        return {"net": [], "day": []}

    def orders(self):
        self.orders_calls += 1
        return []

    def order_history(self, order_id):
        self.order_history_calls += 1
        return []

    def place_order(self, **kwargs):
        self.place_order_calls += 1
        return "order-1"

    def modify_order(self, **kwargs):
        self.modify_order_calls += 1

    def cancel_order(self, **kwargs):
        self.cancel_order_calls += 1


class ReversalClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.symbol = "NIFTY26AUG25000CE"

    def positions(self):
        self.positions_calls += 1
        net = [] if self.symbol is None else [{
            "tradingsymbol": self.symbol,
            "exchange": "NFO",
            "quantity": 65,
            "average_price": 100.0,
            "product": "MIS",
        }]
        return {"net": net, "day": []}

    def place_order(self, **kwargs):
        self.place_order_calls += 1
        order_id = f"order-{self.place_order_calls}"
        if kwargs["transaction_type"] == "SELL":
            self.symbol = None
        else:
            self.symbol = kwargs["tradingsymbol"]
        return order_id

    def order_history(self, order_id):
        self.order_history_calls += 1
        return [{
            "order_id": order_id,
            "status": "COMPLETE",
            "quantity": 65,
            "filled_quantity": 65,
            "pending_quantity": 0,
            "average_price": 90.0,
            "exchange_update_timestamp": "2026-08-25 09:15:00",
        }]


def test_runtime_market_share_one_risk_authority_and_reject_before_broker_activity():
    client = FakeClient()
    limits = LiveRiskLimits(1, 64, 2, 1000)
    injected_pnl = risk_pnl()
    runtime = build_live_runtime(
        client,
        execution_enabled=True,
        risk_limits=limits,
        risk_session_net_pnl_aggregator=injected_pnl,
    )
    market = build_live_market_data(client, object(), runtime)
    market._live_market_data_clock = lambda: NOW
    market.nifty_option_pair = {
        "CE": {"tradingsymbol": "NIFTY26AUG25000CE", "lot_size": 65},
        "PE": {"tradingsymbol": "NIFTY26AUG25000PE", "lot_size": 65},
    }
    market.latest_option_premiums = {"CE": 27.0, "PE": 30.0}
    market.latest_option_timestamps = {"CE": NOW, "PE": NOW}
    runtime.market_data_health_tracker.mark_connected(NOW)
    runtime.market_data_health_tracker.record_valid_tick(NOW, NOW)
    runtime.readiness_gate.apply_recovery_result(
        LiveRecoveryResult(LiveRecoveryState.SAFE_FLAT, (), (), "safe")
    )
    assert runtime.market_data_health_tracker.snapshot(NOW).state is (
        MarketDataHealthState.HEALTHY
    )
    assert market.live_risk_evaluator is runtime.risk_evaluator
    assert market.live_risk_guard is runtime.risk_guard
    assert runtime.risk_evaluator.position_manager is runtime.position_manager
    assert runtime.risk_evaluator.closed_position_history is runtime.closed_position_history
    assert runtime.risk_evaluator.session_net_pnl_aggregator is injected_pnl
    manager_before = runtime.position_manager.active_position
    history_before = runtime.closed_position_history.positions

    result = StrategyResult("risk", SignalAction.BUY_CE, NOW, (SignalAction.BUY_CE,))
    with pytest.raises(LiveRiskViolationError, match="max_order_quantity"):
        market._execute_strategy_result(result, NOW)

    assert client.positions_calls == client.orders_calls == 0
    assert client.order_history_calls == client.place_order_calls == 0
    assert client.modify_order_calls == client.cancel_order_calls == 0
    assert runtime.position_manager.active_position is manager_before
    assert runtime.closed_position_history.positions == history_before
    assert runtime.readiness_gate.is_ready
    assert market.strategy_engine.target_points == 2.0


def test_paper_default_has_no_live_risk_effect():
    from trading.market import MarketData
    from trading.execution_mode import ExecutionMode

    market = MarketData(None, None)
    assert market.execution_router.mode is ExecutionMode.PAPER
    assert market.live_risk_evaluator is None
    assert market.live_risk_guard is None


def test_confirmed_reversal_exit_updates_history_then_opposite_buy_is_rechecked():
    client = ReversalClient()
    runtime = build_live_runtime(
        client,
        execution_enabled=True,
        risk_limits=LiveRiskLimits(1, 65, 1, 1000),
        risk_session_net_pnl_aggregator=risk_pnl(),
    )
    market = build_live_market_data(client, object(), runtime)
    market._live_market_data_clock = lambda: NOW
    market.nifty_option_pair = {
        "CE": {"tradingsymbol": "NIFTY26AUG25000CE", "lot_size": 65},
        "PE": {"tradingsymbol": "NIFTY26AUG25000PE", "lot_size": 65},
    }
    market.latest_option_premiums = {"CE": 90.0, "PE": 95.0}
    market.latest_option_timestamps = {"CE": NOW, "PE": NOW}
    old_position = ManagedPosition(
        PositionSide.CE,
        "NIFTY26AUG25000CE",
        65,
        100.0,
        NOW - timedelta(minutes=5),
        PositionState.OPEN,
    )
    runtime.position_manager.register(old_position)
    market.live_execution_context = LiveExecutionContext(
        "CE", "NIFTY26AUG25000CE", 65
    )
    runtime.market_data_health_tracker.mark_connected(NOW)
    runtime.market_data_health_tracker.record_valid_tick(NOW, NOW)
    runtime.readiness_gate.apply_recovery_result(
        LiveRecoveryResult(LiveRecoveryState.SAFE_FLAT, (), (), "safe")
    )
    reversal = StrategyResult(
        "reversal",
        SignalAction.EXIT_CE,
        NOW,
        actions=(SignalAction.EXIT_CE, SignalAction.BUY_PE),
    )

    with pytest.raises(
        LiveRiskViolationError, match="max_completed_trades_per_day"
    ):
        market._execute_strategy_result(reversal, NOW)

    assert client.positions_calls == 1
    assert client.place_order_calls == 1
    assert client.order_history_calls == 1
    assert client.orders_calls == 0
    assert client.modify_order_calls == client.cancel_order_calls == 0
    assert runtime.position_manager.active_position is None
    assert old_position.state is PositionState.OPEN
    assert len(runtime.closed_position_history.positions) == 1
    assert runtime.closed_position_history.positions[0] is market.latest_closed_position
    assert market.live_execution_context is None
    assert runtime.readiness_gate.is_ready
