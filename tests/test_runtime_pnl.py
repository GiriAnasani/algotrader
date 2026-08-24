from dataclasses import FrozenInstanceError, fields
from datetime import date, datetime, timedelta, timezone
from math import fsum, inf, nan
from pathlib import Path

import pytest

from trading.close_position_lifecycle import ClosedPosition
from trading.ohlc import EXCHANGE_TIMEZONE
from trading.option_charges import (
    NetPnLCalculator,
    OptionChargeSchedule,
    OptionTradeChargesCalculator,
)
from trading.portfolio_net_pnl import PortfolioNetPnLAggregator, PortfolioNetPnLResult
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.realized_net_pnl import RealizedNetPnLAggregator
from trading.runtime_pnl import RuntimePnLSnapshot, RuntimePnLSnapshotBuilder
from trading.session_net_pnl import SessionNetPnLAggregator, SessionNetPnLResult
from trading.unrealized_pnl import UnrealizedPnLAggregator


DAY = date(2026, 8, 24)
NOW = datetime(2026, 8, 24, 9, 15, tzinfo=EXCHANGE_TIMEZONE)


def closed(side=PositionSide.CE, entry=100.0, exit=105.0, exit_time=NOW):
    return ClosedPosition(side, f"NIFTY26AUG25000{side.value}", 65, entry,
                          exit_time - timedelta(minutes=30), exit, exit_time,
                          PositionState.CLOSED)


def active(side=PositionSide.CE, entry=100.0, entry_time=NOW):
    return ManagedPosition(side, f"NIFTY26AUG25000{side.value}", 65, entry,
                           entry_time, PositionState.OPEN)


def dependencies(brokerage=20.0):
    schedule = OptionChargeSchedule(brokerage, .0015, .0003553, .000001, .00003, .18)
    realized = RealizedNetPnLAggregator(
        NetPnLCalculator(OptionTradeChargesCalculator(schedule))
    )
    return PortfolioNetPnLAggregator(realized), SessionNetPnLAggregator(realized)


def builder(brokerage=20.0):
    return RuntimePnLSnapshotBuilder(*dependencies(brokerage))


def snapshot(**overrides):
    values = dict(trading_date=DAY, closed_trade_count=2, open_position_count=1,
                  realized_gross_pnl=100.0, realized_total_charges=20.0,
                  realized_net_pnl=80.0, unrealized_gross_pnl=10.0,
                  portfolio_net_pnl=90.0, session_net_pnl=50.0)
    values.update(overrides)
    return RuntimePnLSnapshot(**values)


@pytest.mark.parametrize("portfolio", [None, object(), True, "portfolio"])
def test_builder_requires_portfolio_aggregator(portfolio):
    _, session = dependencies()
    with pytest.raises(TypeError):
        RuntimePnLSnapshotBuilder(portfolio, session)


@pytest.mark.parametrize("session", [None, object(), True, "session"])
def test_builder_requires_session_aggregator(session):
    portfolio, _ = dependencies()
    with pytest.raises(TypeError):
        RuntimePnLSnapshotBuilder(portfolio, session)


def test_flat_runtime_returns_exact_zeros():
    assert builder().build(DAY, []) == RuntimePnLSnapshot(
        DAY, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    )


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
def test_realized_only_runtime_includes_completed_charges(side):
    result = builder().build(DAY, [closed(side)])
    assert result.closed_trade_count == 1
    assert result.open_position_count == 0
    assert result.realized_gross_pnl == 325
    assert result.realized_total_charges > 0
    assert result.realized_net_pnl == fsum(
        (result.realized_gross_pnl, -result.realized_total_charges)
    )
    assert result.portfolio_net_pnl == result.realized_net_pnl


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
@pytest.mark.parametrize("reference,sign", [(105, 1), (95, -1)])
def test_unrealized_only_runtime_uses_gross_open_pnl(side, reference, sign):
    result = builder().build(DAY, [], active(side), reference)
    assert result.closed_trade_count == 0
    assert result.open_position_count == 1
    assert result.realized_gross_pnl == result.realized_total_charges == 0
    assert result.unrealized_gross_pnl * sign > 0
    assert result.portfolio_net_pnl == result.unrealized_gross_pnl


@pytest.mark.parametrize("exit_price,reference", [(110, 110), (90, 80)])
def test_combined_runtime_has_exact_portfolio_net(exit_price, reference):
    result = builder().build(
        DAY, [closed(entry=100, exit=exit_price)], active(entry=100), reference
    )
    assert result.portfolio_net_pnl == fsum(
        (result.realized_net_pnl, result.unrealized_gross_pnl)
    )


def test_session_excludes_old_history_while_portfolio_includes_it():
    old = closed(exit_time=NOW - timedelta(days=1))
    current = closed(PositionSide.PE)
    all_result = builder().build(DAY, [old, current])
    current_result = builder().build(DAY, [current])
    assert all_result.closed_trade_count == 2
    assert all_result.session_net_pnl == current_result.session_net_pnl
    assert all_result.portfolio_net_pnl != all_result.session_net_pnl


def test_out_of_session_active_is_portfolio_valued_but_session_excluded():
    position = active(entry_time=NOW - timedelta(days=1))
    result = builder().build(DAY, [], position, 105)
    assert result.open_position_count == 1
    assert result.portfolio_net_pnl == 325
    assert result.session_net_pnl == 0


def test_exchange_date_behavior_is_inherited():
    timestamp = datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc)
    assert timestamp.date() == date(2026, 8, 23)
    assert timestamp.astimezone(EXCHANGE_TIMEZONE).date() == DAY

    position = closed(exit_time=timestamp)
    result = builder().build(DAY, [position])
    assert result.session_net_pnl == result.portfolio_net_pnl


def test_utc_active_entry_crossing_into_exchange_date_is_in_both_snapshots(
    monkeypatch,
):
    timestamp = datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc)
    assert timestamp.date() == date(2026, 8, 23)
    assert timestamp.astimezone(EXCHANGE_TIMEZONE).date() == DAY

    position = active(entry_time=timestamp)
    reference_price = 105
    seen = []
    original = UnrealizedPnLAggregator.aggregate

    def spy(self, supplied_position, supplied_reference_price=None):
        seen.append((supplied_position, supplied_reference_price))
        return original(self, supplied_position, supplied_reference_price)

    monkeypatch.setattr(UnrealizedPnLAggregator, "aggregate", spy)
    result = builder().build(DAY, [], position, reference_price)

    assert result.open_position_count == 1
    assert result.unrealized_gross_pnl == 325
    assert result.portfolio_net_pnl == 325
    assert result.session_net_pnl == 325
    assert seen == [(position, reference_price), (position, reference_price)]


def test_exact_arguments_forwarded_to_both_dependencies(monkeypatch):
    portfolio, session = dependencies()
    positions = [closed()]
    position = active()
    portfolio_value = PortfolioNetPnLResult(1, 1, 100, 20, 80, -30, 50)
    session_value = SessionNetPnLResult(DAY, 1, 1, 100, 20, 80, -30, 50)
    portfolio_seen = []
    session_seen = []
    def portfolio_spy(*args):
        portfolio_seen.append(args)
        return portfolio_value
    def session_spy(*args):
        session_seen.append(args)
        return session_value
    monkeypatch.setattr(portfolio, "aggregate", portfolio_spy)
    monkeypatch.setattr(session, "aggregate", session_spy)
    result = RuntimePnLSnapshotBuilder(portfolio, session).build(
        DAY, positions, position, 95, 2, 3
    )
    assert result == RuntimePnLSnapshot(DAY, 1, 1, 100, 20, 80, -30, 50, 50)
    assert portfolio_seen == [(positions, position, 95, 2, 3)]
    assert session_seen == [(DAY, positions, position, 95, 2, 3)]


def test_out_of_session_active_passes_flat_view_to_session(monkeypatch):
    portfolio, session = dependencies()
    position = active(entry_time=NOW - timedelta(days=1))
    seen = []
    original = session.aggregate
    def spy(*args):
        seen.append(args)
        return original(*args)
    monkeypatch.setattr(session, "aggregate", spy)
    RuntimePnLSnapshotBuilder(portfolio, session).build(DAY, [], position, 105)
    assert seen == [(DAY, [], None, None, 1, 1)]


def test_custom_order_counts_change_runtime_realized_values():
    positions = [closed()]
    normal = builder().build(DAY, positions)
    custom = builder().build(DAY, positions, buy_order_count=2, sell_order_count=4)
    assert custom.realized_total_charges > normal.realized_total_charges
    assert custom.realized_net_pnl < normal.realized_net_pnl


@pytest.mark.parametrize("count", [None, True, False, 0, -1, 1.5, "1"])
@pytest.mark.parametrize("argument", ["buy_order_count", "sell_order_count"])
def test_invalid_order_counts_propagate(argument, count):
    with pytest.raises(ValueError):
        builder().build(DAY, [], **{argument: count})


@pytest.mark.parametrize("invalid", [None, "2026-08-24", datetime(2026, 8, 24), True])
def test_strict_trading_date_validation_propagates(invalid):
    with pytest.raises(TypeError):
        builder().build(invalid, [])


@pytest.mark.parametrize("positions", [None, {}, "positions", {1}, object(), [object()]])
def test_invalid_history_propagates(positions):
    with pytest.raises(TypeError):
        builder().build(DAY, positions)


@pytest.mark.parametrize("position", [object(), True, "position"])
def test_invalid_active_position_propagates(position):
    with pytest.raises(TypeError):
        builder().build(DAY, [], position, 105)


def test_missing_reference_price_propagates():
    with pytest.raises(ValueError):
        builder().build(DAY, [], active())


@pytest.mark.parametrize("price", [True, 0, -1, nan, inf, "105"])
def test_invalid_reference_price_propagates(price):
    with pytest.raises(ValueError):
        builder().build(DAY, [], active(), price)


def test_snapshot_is_frozen_with_exact_fields():
    value = snapshot()
    assert [field.name for field in fields(value)] == [
        "trading_date", "closed_trade_count", "open_position_count",
        "realized_gross_pnl", "realized_total_charges", "realized_net_pnl",
        "unrealized_gross_pnl", "portfolio_net_pnl", "session_net_pnl"]
    with pytest.raises(FrozenInstanceError):
        value.session_net_pnl = 0


@pytest.mark.parametrize("invalid", [None, "2026-08-24", datetime(2026, 8, 24), True])
def test_snapshot_requires_strict_date(invalid):
    with pytest.raises(TypeError):
        snapshot(trading_date=invalid)


@pytest.mark.parametrize("field,bad", [
    ("closed_trade_count", -1), ("closed_trade_count", True),
    ("open_position_count", -1), ("open_position_count", 2),
    ("open_position_count", True)])
def test_snapshot_rejects_invalid_counts(field, bad):
    with pytest.raises(ValueError):
        snapshot(**{field: bad})


@pytest.mark.parametrize("field", ["realized_gross_pnl", "realized_total_charges",
    "realized_net_pnl", "unrealized_gross_pnl", "portfolio_net_pnl",
    "session_net_pnl"])
@pytest.mark.parametrize("bad", [True, nan, inf, -inf, object()])
def test_snapshot_rejects_invalid_numbers(field, bad):
    with pytest.raises(ValueError):
        snapshot(**{field: bad})


def test_snapshot_rejects_negative_charges_and_inconsistent_portfolio():
    with pytest.raises(ValueError):
        snapshot(realized_total_charges=-1)
    with pytest.raises(ValueError):
        snapshot(realized_net_pnl=81)
    with pytest.raises(ValueError):
        snapshot(portfolio_net_pnl=91)


def test_runtime_module_has_no_execution_or_state_side_effect_dependencies():
    source = Path("trading/runtime_pnl.py").read_text(encoding="utf-8").lower()
    forbidden = ("positionmanager", "positionstore", "brokerposition", "kite",
                 "zerodha", "marketdata", "websocket", "strategy", "readiness",
                 "authorization", "place_order", "execution", "enabled", "persist",
                 "save(", "database", "today(", "now(", "risk", "margin", "capital",
                 "account", "positionpnlcalculator", "optiontradechargescalculator")
    assert all(term not in source for term in forbidden)
