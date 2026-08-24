from datetime import datetime, timedelta, timezone

import pytest

from trading.broker_order_status import BrokerOrderState, BrokerOrderStatus
from trading.broker_position_reconciler import BrokerPositionReconciler
from trading.execution_mode import ExecutionMode
from trading.live_execution import LiveExecutionCoordinator
from trading.live_order import LiveOrderIntent
from trading.live_readiness import LiveReadinessGate, LiveReadinessState
from trading.market import LivePositionLifecycleError, MarketData
from trading.position import PositionSide
from trading.position_manager import PositionManager
from trading.position_store import PositionStore, PositionStoreError
from trading.option_charges import (
    NetPnLCalculator,
    OptionChargeSchedule,
    OptionTradeChargesCalculator,
)
from trading.portfolio_net_pnl import PortfolioNetPnLAggregator
from trading.realized_net_pnl import RealizedNetPnLAggregator
from trading.runtime_pnl import RuntimePnLSnapshotBuilder
from trading.runtime_pnl_state import RuntimePnLService, RuntimePnLStateAdapter
from trading.session_net_pnl import SessionNetPnLAggregator
from trading.strategy import SignalAction
from trading.zerodha_order_adapter import ZerodhaOrderAdapter
from trading.zerodha_order_status_reader import ZerodhaOrderStatusReader
from trading.zerodha_order_submitter import ZerodhaOrderSubmitter
from trading.zerodha_position_reader import ZerodhaPositionReader

NOW = datetime(2026, 8, 24, 9, 15, tzinfo=timezone.utc)


class Client:
    def __init__(self):
        self.positions_calls = self.orders_calls = 0
        self.order_history_calls = self.place_order_calls = 0

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


def make_market(store=None):
    client = Client()
    manager = PositionManager()
    gate = LiveReadinessGate()
    value = MarketData(
        None, None, execution_mode=ExecutionMode.LIVE,
        live_execution_coordinator=LiveExecutionCoordinator(
            ZerodhaOrderAdapter(), ZerodhaOrderSubmitter(client), enabled=False),
        live_order_status_reader=ZerodhaOrderStatusReader(client),
        live_position_reader=ZerodhaPositionReader(client),
        live_position_reconciler=BrokerPositionReconciler(),
        live_readiness_gate=gate, live_position_manager=manager,
        live_position_store=store,
    )
    return value, manager, gate, client


def intent(action, side):
    return LiveOrderIntent(f"NIFTY26AUG25000{side}", side, action, 65, 100,
                           NOW - timedelta(seconds=1))


def status(state=BrokerOrderState.COMPLETE, timestamp=NOW, price=105):
    filled = 65 if state is BrokerOrderState.COMPLETE else 0
    pending = 0 if state is BrokerOrderState.COMPLETE else 65
    return BrokerOrderStatus("order-1", state, filled, pending, price, state.value,
                             fill_timestamp=timestamp)


def apply(market, order, broker_status=None):
    return market._apply_live_order_status("order-1", order, broker_status or status())


def snapshot_builder():
    schedule = OptionChargeSchedule(20, .0015, .0003553, .000001, .00003, .18)
    realized = RealizedNetPnLAggregator(
        NetPnLCalculator(OptionTradeChargesCalculator(schedule))
    )
    return RuntimePnLSnapshotBuilder(
        PortfolioNetPnLAggregator(realized), SessionNetPnLAggregator(realized)
    )


@pytest.mark.parametrize("side,buy,exit_action", [
    ("CE", SignalAction.BUY_CE, SignalAction.EXIT_CE),
    ("PE", SignalAction.BUY_PE, SignalAction.EXIT_PE)])
def test_confirmed_exit_records_exactly_once_and_preserves_runtime_truth(
    side, buy, exit_action
):
    market, manager, _, client = make_market()
    apply(market, intent(buy, side))
    calls = vars(client).copy()
    apply(market, intent(exit_action, side))
    history = market.live_closed_position_history.positions
    assert len(history) == 1
    assert history[0] is market.latest_closed_position
    assert RuntimePnLStateAdapter(market).view().closed_positions[0] is history[0]
    assert manager.active_position is None
    assert market.live_execution_context is None
    assert vars(client) == calls


@pytest.mark.parametrize("state", [BrokerOrderState.OPEN, BrokerOrderState.REJECTED,
                                    BrokerOrderState.CANCELLED])
def test_unconfirmed_exit_records_nothing(state):
    market, manager, _, _ = make_market()
    apply(market, intent(SignalAction.BUY_CE, "CE"))
    active = manager.active_position
    apply(market, intent(SignalAction.EXIT_CE, "CE"), status(state=state))
    assert market.live_closed_position_history.positions == ()
    assert manager.active_position is active


def test_missing_fill_timestamp_records_nothing_and_fails_closed():
    market, manager, gate, _ = make_market()
    apply(market, intent(SignalAction.BUY_CE, "CE"))
    active = manager.active_position
    with pytest.raises(LivePositionLifecycleError):
        apply(market, intent(SignalAction.EXIT_CE, "CE"), status(timestamp=None))
    assert market.live_closed_position_history.positions == ()
    assert manager.active_position is active
    assert gate.state is LiveReadinessState.NOT_READY


def test_persistence_failure_keeps_confirmed_history_and_flat_truth(tmp_path):
    store = PositionStore(tmp_path / "position.json")
    market, manager, gate, _ = make_market(store)
    apply(market, intent(SignalAction.BUY_CE, "CE"))
    store.save = lambda manager: (_ for _ in ()).throw(PositionStoreError("failed"))
    with pytest.raises(PositionStoreError):
        apply(market, intent(SignalAction.EXIT_CE, "CE"))
    assert manager.active_position is None
    assert len(market.live_closed_position_history.positions) == 1
    assert market.latest_closed_position is market.live_closed_position_history.positions[0]
    assert gate.state is LiveReadinessState.NOT_READY


def test_reversal_and_later_exit_retain_exact_order():
    market, manager, _, _ = make_market()
    apply(market, intent(SignalAction.BUY_CE, "CE"))
    apply(market, intent(SignalAction.EXIT_CE, "CE"))
    first = market.latest_closed_position
    assert manager.active_position is None
    apply(market, intent(SignalAction.BUY_PE, "PE"))
    assert market.live_closed_position_history.positions == (first,)
    apply(market, intent(SignalAction.EXIT_PE, "PE"))
    second = market.latest_closed_position
    assert market.live_closed_position_history.positions == (first, second)


def test_three_completed_trades_are_retained_and_used_by_runtime_pnl():
    market, _, _, _ = make_market()
    sequence = [("CE", SignalAction.BUY_CE, SignalAction.EXIT_CE),
                ("PE", SignalAction.BUY_PE, SignalAction.EXIT_PE),
                ("CE", SignalAction.BUY_CE, SignalAction.EXIT_CE)]
    for side, buy, exit_action in sequence:
        apply(market, intent(buy, side))
        apply(market, intent(exit_action, side))
    history = market.live_closed_position_history.positions
    view = RuntimePnLStateAdapter(market).view()
    result = RuntimePnLService(
        RuntimePnLStateAdapter(market), snapshot_builder()
    ).snapshot(NOW.date())
    assert len(history) == result.closed_trade_count == 3
    assert view.closed_positions == history
    assert all(left is right for left, right in zip(view.closed_positions, history))
    assert market.latest_closed_position is history[-1]
    assert result.realized_gross_pnl == 0
    assert result.realized_net_pnl < 0
