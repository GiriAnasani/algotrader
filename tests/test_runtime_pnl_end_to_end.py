import json
from datetime import datetime, timedelta, timezone

from trading.closed_position_history import ClosedPositionHistory
from trading.closed_position_history_store import ClosedPositionHistoryStore
from trading.close_position_lifecycle import ClosedPosition
from trading.live_restart_orchestration import LiveRestartOrchestrator
from trading.live_run_authorization import LiveRunAuthorizer
from trading.live_startup import initialize_live_session
from trading.option_charges import OptionChargeSchedule
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import PositionManager
from trading.position_store import PositionStore
from trading.runtime_pnl_composition import build_runtime_pnl_composition
from trading.runtime_pnl_state import RuntimePnLStateAdapter


NOW = datetime(2026, 8, 24, 9, 15, tzinfo=timezone.utc)


class Client:
    def __init__(self, positions):
        self.net = list(positions)
        self.positions_calls = self.orders_calls = 0
        self.order_history_calls = self.place_order_calls = 0
        self.modify_order_calls = self.cancel_order_calls = 0
    def positions(self):
        self.positions_calls += 1
        return {"net": self.net, "day": []}
    def orders(self):
        self.orders_calls += 1
        return []
    def order_history(self, order_id):
        self.order_history_calls += 1
        return []
    def place_order(self, **kwargs):
        self.place_order_calls += 1
        return "order"
    def modify_order(self, **kwargs): self.modify_order_calls += 1
    def cancel_order(self, **kwargs): self.cancel_order_calls += 1
    @property
    def counters(self):
        return (self.positions_calls, self.orders_calls, self.order_history_calls,
                self.place_order_calls, self.modify_order_calls,
                self.cancel_order_calls)


def closed(side, exit_time, entry, exit):
    return ClosedPosition(side, f"NIFTY26AUG25000{side.value}", 65, entry,
                          exit_time - timedelta(minutes=10), exit, exit_time,
                          PositionState.CLOSED)


def test_fresh_process_restart_to_report_preserves_all_boundaries(tmp_path):
    process_a_history = ClosedPositionHistory()
    old = closed(PositionSide.CE, NOW - timedelta(days=1), 100, 105)
    current = closed(PositionSide.PE, NOW, 105, 100)
    process_a_history.record(old); process_a_history.record(current)
    history_store = ClosedPositionHistoryStore(tmp_path / "history.json")
    history_store.save(process_a_history)

    process_a_active = ManagedPosition(
        PositionSide.CE, "NIFTY26AUG25000CE", 65, 100, NOW, PositionState.OPEN)
    position_store = PositionStore(tmp_path / "position.json")
    process_a_manager = PositionManager(); process_a_manager.register(process_a_active)
    position_store.save(process_a_manager)
    broker = {"tradingsymbol": process_a_active.contract_symbol, "exchange": "NFO",
              "quantity": 65, "average_price": 100, "product": "MIS"}

    client = Client([broker])
    startup = initialize_live_session(
        client, object(), execution_enabled=False,
        closed_position_history_store=history_store)
    restart = LiveRestartOrchestrator(position_store, history_store).orchestrate(startup)
    process_b_view = RuntimePnLStateAdapter(startup.market).view()
    process_b_active = restart.restoration_result.restored_position

    assert process_b_active is startup.runtime.position_manager.active_position
    assert process_b_active is startup.market.live_position_manager.active_position
    assert process_b_active is process_b_view.active_position
    assert process_b_active is not process_a_active
    assert process_b_view.closed_positions == (old, current)
    assert all(new is not old_value for new, old_value in
               zip(process_b_view.closed_positions, (old, current)))
    assert all(new is owned for new, owned in zip(
        process_b_view.closed_positions,
        startup.runtime.closed_position_history.positions))

    position_bytes = position_store.path.read_bytes()
    history_bytes = history_store.path.read_bytes()
    broker_before = client.counters
    readiness_before = startup.runtime.readiness_gate.state
    enabled_before = startup.runtime.execution_coordinator.enabled
    authorization_before = LiveRunAuthorizer().authorize(startup).state
    manager_before = startup.runtime.position_manager.active_position
    history_before = startup.runtime.closed_position_history.positions
    latest_before = startup.market.latest_closed_position
    context_before = startup.market.live_execution_context
    pending_before = startup.market.pending_live_order

    composition = build_runtime_pnl_composition(
        startup.market, OptionChargeSchedule.zerodha_nse_options_2026())
    snapshot = composition.snapshot(NOW.date(), reference_price=105)
    report = composition.report(NOW.date(), reference_price=105)
    document = report.to_dict()

    assert snapshot.closed_trade_count == report.closed_trade_count == 2
    assert snapshot.open_position_count == report.open_position_count == 1
    assert report.unrealized_gross_pnl == 325
    assert report.realized_total_charges > 0
    assert report.portfolio_net_pnl != report.session_net_pnl
    assert json.loads(report.to_json()) == document
    assert document["trading_date"] == "2026-08-24"
    assert all(not isinstance(value, (ManagedPosition, ClosedPositionHistory))
               for value in document.values())

    assert client.counters == broker_before == (1, 1, 0, 0, 0, 0)
    assert position_store.path.read_bytes() == position_bytes
    assert history_store.path.read_bytes() == history_bytes
    assert startup.runtime.readiness_gate.state is readiness_before
    assert startup.runtime.execution_coordinator.enabled is enabled_before is False
    assert LiveRunAuthorizer().authorize(startup).state is authorization_before
    assert startup.runtime.position_manager.active_position is manager_before
    assert startup.runtime.closed_position_history.positions == history_before
    assert all(left is right for left, right in zip(
        startup.runtime.closed_position_history.positions, history_before))
    assert startup.market.latest_closed_position is latest_before
    assert startup.market.live_execution_context is context_before
    assert startup.market.pending_live_order is pending_before
