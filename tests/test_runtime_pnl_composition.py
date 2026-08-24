import json
from dataclasses import FrozenInstanceError, fields
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from trading.closed_position_history import ClosedPositionHistory
from trading.close_position_lifecycle import ClosedPosition
from trading.execution_mode import ExecutionMode
from trading.market import MarketData
from trading.ohlc import EXCHANGE_TIMEZONE
from trading.option_charges import OptionChargeSchedule
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import PositionManager
from trading.runtime_pnl import RuntimePnLSnapshotBuilder
from trading.runtime_pnl_composition import (
    RuntimePnLComposition,
    build_runtime_pnl_composition,
)
from trading.runtime_pnl_report import RuntimePnLReporter
from trading.runtime_pnl_state import (
    PaperRuntimePnLStateUnsupportedError,
    RuntimePnLService,
    RuntimePnLStateAdapter,
)


DAY = date(2026, 8, 24)
NOW = datetime(2026, 8, 24, 9, 15, tzinfo=EXCHANGE_TIMEZONE)
SCHEDULE = OptionChargeSchedule.zerodha_nse_options_2026()


class Router:
    def __init__(self, mode): self.mode = mode


def market(mode=ExecutionMode.LIVE, active=None, closed=()):
    value = object.__new__(MarketData)
    value.execution_router = Router(mode)
    value.live_position_manager = PositionManager()
    if active is not None: value.live_position_manager.register(active)
    value.live_closed_position_history = ClosedPositionHistory()
    for position in closed: value.live_closed_position_history.record(position)
    value.latest_closed_position = closed[-1] if closed else None
    value.live_execution_context = object() if active else None
    value.pending_live_order = None
    return value


def active(side=PositionSide.CE, entry=100, entry_time=NOW):
    return ManagedPosition(side, f"NIFTY26AUG25000{side.value}", 65, entry,
                           entry_time, PositionState.OPEN)


def closed(side=PositionSide.CE, entry=100, exit=105, exit_time=NOW):
    return ClosedPosition(side, f"NIFTY26AUG25000{side.value}", 65, entry,
                          exit_time - timedelta(minutes=5), exit, exit_time,
                          PositionState.CLOSED)


@pytest.mark.parametrize("value", [None, object(), True, "market"])
def test_builder_requires_market_data(value):
    with pytest.raises(TypeError):
        build_runtime_pnl_composition(value, SCHEDULE)


@pytest.mark.parametrize("value", [None, object(), True, "schedule"])
def test_builder_requires_explicit_charge_schedule(value):
    with pytest.raises(TypeError):
        build_runtime_pnl_composition(market(), value)


def test_composition_is_frozen_and_retains_exact_authority_and_components():
    authority = market()
    composition = build_runtime_pnl_composition(authority, SCHEDULE)
    assert [field.name for field in fields(composition)] == [
        "state_adapter", "snapshot_builder", "service", "reporter"]
    assert composition.state_adapter._market is authority
    assert isinstance(composition.snapshot_builder, RuntimePnLSnapshotBuilder)
    assert isinstance(composition.service, RuntimePnLService)
    assert isinstance(composition.reporter, RuntimePnLReporter)
    with pytest.raises(FrozenInstanceError): composition.reporter = RuntimePnLReporter()


@pytest.mark.parametrize("field,value", [
    ("state_adapter", object()), ("snapshot_builder", object()),
    ("service", object()), ("reporter", object())])
def test_bundle_rejects_invalid_components(field, value):
    composition = build_runtime_pnl_composition(market(), SCHEDULE)
    values = {item.name: getattr(composition, item.name) for item in fields(composition)}
    values[field] = value
    with pytest.raises(TypeError): RuntimePnLComposition(**values)


def test_flat_end_to_end_snapshot_report_dict_and_json():
    composition = build_runtime_pnl_composition(market(), SCHEDULE)
    snapshot = composition.snapshot(DAY)
    report = composition.report(DAY)
    assert snapshot.closed_trade_count == snapshot.open_position_count == 0
    assert report.to_dict() == {
        "trading_date": "2026-08-24", "closed_trade_count": 0,
        "open_position_count": 0, "realized_gross_pnl": 0.0,
        "realized_total_charges": 0.0, "realized_net_pnl": 0.0,
        "unrealized_gross_pnl": 0.0, "portfolio_net_pnl": 0.0,
        "session_net_pnl": 0.0}
    assert json.loads(report.to_json()) == report.to_dict()


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
@pytest.mark.parametrize("reference,expected", [(105, 325), (95, -325)])
def test_active_positions_require_and_use_explicit_price(side, reference, expected):
    position = active(side)
    authority = market(active=position)
    composition = build_runtime_pnl_composition(authority, SCHEDULE)
    assert composition.report(DAY, reference).unrealized_gross_pnl == expected
    assert authority.live_position_manager.active_position is position
    with pytest.raises(ValueError): composition.report(DAY)


def test_complete_history_and_active_are_aggregated_without_mutation():
    history = (closed(PositionSide.CE), closed(PositionSide.PE, 105, 100))
    position = active()
    authority = market(active=position, closed=history)
    composition = build_runtime_pnl_composition(authority, SCHEDULE)
    before = authority.live_closed_position_history.positions
    report = composition.report(DAY, 102)
    assert report.closed_trade_count == 2
    assert report.realized_gross_pnl == 0
    assert report.realized_total_charges > 0
    assert report.realized_net_pnl < 0
    assert report.unrealized_gross_pnl == 130
    assert report.portfolio_net_pnl == report.realized_net_pnl + 130
    assert authority.live_closed_position_history.positions == before
    assert all(left is right for left, right in zip(before, history))


def test_session_excludes_old_history_while_portfolio_includes_it():
    old = closed(exit_time=NOW - timedelta(days=1))
    current = closed(PositionSide.PE)
    composition = build_runtime_pnl_composition(
        market(active=active(), closed=(old, current)), SCHEDULE)
    report = composition.report(DAY, 105)
    current_only = build_runtime_pnl_composition(
        market(active=active(), closed=(current,)), SCHEDULE).report(DAY, 105)
    assert report.closed_trade_count == 2
    assert report.session_net_pnl == current_only.session_net_pnl
    assert report.portfolio_net_pnl != report.session_net_pnl


def test_construction_and_reporting_do_not_touch_unrelated_state():
    position = active()
    authority = market(active=position, closed=(closed(),))
    before = vars(authority).copy()
    composition = build_runtime_pnl_composition(authority, SCHEDULE)
    composition.snapshot(DAY, 105); composition.report(DAY, 105)
    assert vars(authority) == before


def test_paper_remains_explicitly_unsupported():
    composition = build_runtime_pnl_composition(market(ExecutionMode.PAPER), SCHEDULE)
    with pytest.raises(PaperRuntimePnLStateUnsupportedError): composition.report(DAY)


def test_source_is_composition_only_without_formulas_or_side_effects():
    source = Path("trading/runtime_pnl_composition.py").read_text(encoding="utf-8").lower()
    forbidden = ("kite", "zerodha", "positionstore", "closedpositionhistorystore",
                 ".load(", ".save(", "websocket", "strategy", "price lookup",
                 "readiness", "authorization", "execution_enabled", "risk", "capital",
                 "margin", "account", "http", "server", "entry_price", "exit_price",
                 "gross_pnl -", "realized_net_pnl +")
    assert all(term not in source for term in forbidden)
