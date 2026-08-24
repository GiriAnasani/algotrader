from dataclasses import FrozenInstanceError, fields
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from trading.close_position_lifecycle import (
    ClosePositionLifecycle,
    ConfirmedPositionExit,
    ClosedPosition,
)
from trading.execution_mode import ExecutionMode
from trading.live_restart_orchestration import LiveRestartOrchestrator
from trading.live_startup import initialize_live_session
from trading.market import MarketData
from trading.ohlc import EXCHANGE_TIMEZONE
from trading.open_position_lifecycle import ConfirmedPositionEntry, OpenPositionLifecycle
from trading.option_charges import (
    NetPnLCalculator,
    OptionChargeSchedule,
    OptionTradeChargesCalculator,
)
from trading.portfolio_net_pnl import PortfolioNetPnLAggregator
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import PositionManager
from trading.position_store import PositionStore
from trading.realized_net_pnl import RealizedNetPnLAggregator
from trading.runtime_pnl import RuntimePnLSnapshotBuilder
from trading.runtime_pnl_state import (
    PaperRuntimePnLStateUnsupportedError,
    RuntimePnLService,
    RuntimePnLStateAdapter,
    RuntimePnLStateView,
)
from trading.session_net_pnl import SessionNetPnLAggregator


DAY = date(2026, 8, 24)
NOW = datetime(2026, 8, 24, 9, 15, tzinfo=EXCHANGE_TIMEZONE)


class Router:
    def __init__(self, mode):
        self.mode = mode


class FakeKiteClient:
    def __init__(self, positions):
        self.net_positions = list(positions)
        self.positions_calls = 0
        self.orders_calls = 0
        self.order_history_calls = 0
        self.place_order_calls = 0

    def positions(self):
        self.positions_calls += 1
        return {"net": self.net_positions, "day": []}

    def orders(self):
        self.orders_calls += 1
        return []

    def order_history(self, order_id):
        self.order_history_calls += 1
        return []

    def place_order(self, **kwargs):
        self.place_order_calls += 1
        return "order"


def market(mode=ExecutionMode.LIVE, manager=None, latest=None):
    value = object.__new__(MarketData)
    value.execution_router = Router(mode)
    value.live_position_manager = manager or PositionManager()
    value.latest_closed_position = latest
    value.paper_trade_ledger = object()
    return value


def managed(side=PositionSide.CE, entry=100.0):
    return ManagedPosition(side, f"NIFTY26AUG25000{side.value}", 65, entry, NOW,
                           PositionState.OPEN)


def closed(side=PositionSide.CE, entry=100.0, exit=105.0):
    return ClosedPosition(side, f"NIFTY26AUG25000{side.value}", 65, entry, NOW,
                          exit, NOW + timedelta(minutes=5), PositionState.CLOSED)


def snapshot_builder():
    schedule = OptionChargeSchedule(20, .0015, .0003553, .000001, .00003, .18)
    realized = RealizedNetPnLAggregator(
        NetPnLCalculator(OptionTradeChargesCalculator(schedule))
    )
    return RuntimePnLSnapshotBuilder(
        PortfolioNetPnLAggregator(realized), SessionNetPnLAggregator(realized)
    )


def service(runtime_market):
    return RuntimePnLService(
        RuntimePnLStateAdapter(runtime_market), snapshot_builder()
    )


def test_state_view_is_frozen_with_exact_fields_and_tuple():
    position = managed()
    finished = closed()
    view = RuntimePnLStateView((finished,), position)
    assert [field.name for field in fields(view)] == [
        "closed_positions", "active_position"]
    assert isinstance(view.closed_positions, tuple)
    assert view.closed_positions[0] is finished
    assert view.active_position is position
    with pytest.raises(FrozenInstanceError):
        view.active_position = None


@pytest.mark.parametrize("history", [[], [closed()], (object(),), None, "history"])
def test_state_view_rejects_invalid_history(history):
    with pytest.raises(TypeError):
        RuntimePnLStateView(history, None)


@pytest.mark.parametrize("position", [object(), True, "position"])
def test_state_view_rejects_invalid_active(position):
    with pytest.raises(TypeError):
        RuntimePnLStateView((), position)


def test_state_view_rejects_non_open_active():
    position = managed()
    object.__setattr__(position, "state", PositionState.CLOSED)
    with pytest.raises(ValueError):
        RuntimePnLStateView((), position)


@pytest.mark.parametrize("value", [None, object(), True, "market"])
def test_adapter_requires_market_data(value):
    with pytest.raises(TypeError):
        RuntimePnLStateAdapter(value)


def test_flat_live_view_and_snapshot_are_exactly_flat():
    runtime_market = market()
    view = RuntimePnLStateAdapter(runtime_market).view()
    assert view == RuntimePnLStateView((), None)
    result = service(runtime_market).snapshot(DAY)
    assert result.closed_trade_count == result.open_position_count == 0
    assert result.portfolio_net_pnl == result.session_net_pnl == 0.0


def test_manager_registered_live_position_identity_is_preserved_and_valued():
    manager = PositionManager()
    restored = managed()
    manager.register(restored)
    runtime_market = market(manager=manager)
    view = RuntimePnLStateAdapter(runtime_market).view()
    result = service(runtime_market).snapshot(DAY, 105)
    assert manager.active_position is restored
    assert view.active_position is restored
    assert result.open_position_count == 1
    assert result.unrealized_gross_pnl == 325
    assert manager.active_position is restored


def test_real_restart_restoration_identity_and_pnl_add_no_broker_activity(tmp_path):
    persisted_source = managed()
    store = PositionStore(tmp_path / "position.json")
    stored_manager = PositionManager()
    stored_manager.register(persisted_source)
    store.save(stored_manager)
    broker_position = {
        "tradingsymbol": persisted_source.contract_symbol,
        "exchange": "NFO",
        "quantity": persisted_source.quantity,
        "average_price": 105.0,
        "product": "MIS",
    }
    client = FakeKiteClient([broker_position])
    startup = initialize_live_session(client, object(), execution_enabled=False)
    orchestration = LiveRestartOrchestrator(store).orchestrate(startup)

    restart_result = orchestration.restart_result
    restoration_result = orchestration.restoration_result
    continuity_result = orchestration.continuity_result
    view = RuntimePnLStateAdapter(startup.market).view()
    restored = restart_result.persisted_position
    assert restored is restoration_result.restored_position
    assert restored is continuity_result.runtime_position
    assert restored is startup.runtime.position_manager.active_position
    assert restored is startup.market.live_position_manager.active_position
    assert restored is view.active_position

    calls_before = (
        client.positions_calls,
        client.orders_calls,
        client.order_history_calls,
        client.place_order_calls,
    )
    result = RuntimePnLService(
        RuntimePnLStateAdapter(startup.market), snapshot_builder()
    ).snapshot(DAY, reference_price=105)
    calls_after = (
        client.positions_calls,
        client.orders_calls,
        client.order_history_calls,
        client.place_order_calls,
    )

    assert result.open_position_count == 1
    assert result.unrealized_gross_pnl == 325
    assert calls_after == calls_before == (1, 1, 0, 0)


def test_confirmed_buy_exposes_exact_lifecycle_position_without_mutation():
    manager = PositionManager()
    runtime_market = market(manager=manager)
    entry = ConfirmedPositionEntry(PositionSide.CE, "NIFTY26AUG25000CE", 65,
                                   100, NOW)
    opened = OpenPositionLifecycle(manager).open(entry)
    view = RuntimePnLStateAdapter(runtime_market).view()
    assert view.active_position is opened
    assert manager.active_position is opened
    assert service(runtime_market).snapshot(DAY, 105).unrealized_gross_pnl == 325
    assert manager.active_position is opened


def test_confirmed_exit_exposes_only_exact_latest_owned_position():
    manager = PositionManager()
    manager.register(managed())
    runtime_market = market(manager=manager)
    exit_fill = ConfirmedPositionExit(PositionSide.CE, "NIFTY26AUG25000CE", 65,
                                      105, NOW + timedelta(minutes=5))
    latest = ClosePositionLifecycle(manager).close(exit_fill)
    runtime_market.latest_closed_position = latest
    view = RuntimePnLStateAdapter(runtime_market).view()
    result = service(runtime_market).snapshot(DAY)
    assert view.active_position is None
    assert view.closed_positions == (latest,)
    assert view.closed_positions[0] is latest
    assert result.closed_trade_count == 1
    assert result.realized_gross_pnl == 325


def test_live_history_is_explicitly_limited_to_latest_owned_position():
    latest = closed(PositionSide.PE)
    imaginary_older = closed(PositionSide.CE)
    runtime_market = market(latest=latest)
    view = RuntimePnLStateAdapter(runtime_market).view()
    assert view.closed_positions == (latest,)
    assert imaginary_older not in view.closed_positions
    assert len(view.closed_positions) == 1


def test_reversal_views_authoritative_flat_then_exact_opposite_open():
    manager = PositionManager()
    old = managed(PositionSide.CE)
    manager.register(old)
    runtime_market = market(manager=manager)
    exit_fill = ConfirmedPositionExit(PositionSide.CE, "NIFTY26AUG25000CE", 65,
                                      105, NOW + timedelta(minutes=5))
    latest = ClosePositionLifecycle(manager).close(exit_fill)
    runtime_market.latest_closed_position = latest
    between = RuntimePnLStateAdapter(runtime_market).view()
    assert between.active_position is None
    assert between.closed_positions == (latest,)

    entry = ConfirmedPositionEntry(PositionSide.PE, "NIFTY26AUG25000PE", 65,
                                   95, NOW + timedelta(minutes=6))
    opposite = OpenPositionLifecycle(manager).open(entry)
    after = RuntimePnLStateAdapter(runtime_market).view()
    assert after.active_position is opposite
    assert after.closed_positions == (latest,)


def test_paper_mode_is_explicitly_unsupported_without_conversion():
    runtime_market = market(ExecutionMode.PAPER)
    with pytest.raises(PaperRuntimePnLStateUnsupportedError, match="PAPER"):
        RuntimePnLStateAdapter(runtime_market).view()


@pytest.mark.parametrize("adapter", [None, object(), True, "adapter"])
def test_service_requires_adapter(adapter):
    with pytest.raises(TypeError):
        RuntimePnLService(adapter, snapshot_builder())


@pytest.mark.parametrize("builder_value", [None, object(), True, "builder"])
def test_service_requires_snapshot_builder(builder_value):
    with pytest.raises(TypeError):
        RuntimePnLService(RuntimePnLStateAdapter(market()), builder_value)


def test_service_forwards_exact_state_and_arguments(monkeypatch):
    manager = PositionManager()
    position = managed()
    manager.register(position)
    latest = closed()
    runtime_market = market(manager=manager, latest=latest)
    builder_value = snapshot_builder()
    seen = []
    expected = builder_value.build(DAY, (latest,), position, 105, 2, 3)
    def spy(*args):
        seen.append(args)
        return expected
    monkeypatch.setattr(builder_value, "build", spy)
    result = RuntimePnLService(
        RuntimePnLStateAdapter(runtime_market), builder_value
    ).snapshot(DAY, 105, 2, 3)
    assert result is expected
    assert seen == [(DAY, (latest,), position, 105, 2, 3)]
    assert seen[0][1][0] is latest
    assert seen[0][2] is position


def test_price_validation_is_delegated_without_lookup():
    runtime_market = market()
    assert service(runtime_market).snapshot(DAY).open_position_count == 0
    manager = runtime_market.live_position_manager
    position = managed()
    manager.register(position)
    with pytest.raises(ValueError):
        service(runtime_market).snapshot(DAY)
    with pytest.raises(ValueError):
        service(runtime_market).snapshot(DAY, -1)
    assert manager.active_position is position


def test_view_and_snapshot_do_not_change_existing_runtime_state():
    manager = PositionManager()
    position = managed()
    manager.register(position)
    runtime_market = market(manager=manager)
    sentinel_ledger = runtime_market.paper_trade_ledger
    before = vars(runtime_market).copy()
    adapter = RuntimePnLStateAdapter(runtime_market)
    adapter.view()
    service(runtime_market).snapshot(DAY, 105)
    assert manager.active_position is position
    assert runtime_market.paper_trade_ledger is sentinel_ledger
    assert vars(runtime_market) == before


def test_source_has_no_broker_persistence_execution_or_price_activity():
    source = Path("trading/runtime_pnl_state.py").read_text(encoding="utf-8").lower()
    forbidden = ("kite", "zerodha", "positions(", "orders(", "order_history",
                 "place_order", "modify_order", "cancel_order", "quote", "ltp",
                 "positionstore", ".save(", ".load(", "websocket", ".start(",
                 "readiness", "authorization", "execution_enabled", "risk",
                 "margin", "capital", "account", "latest_option_premium")
    assert all(term not in source for term in forbidden)
