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
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.realized_net_pnl import RealizedNetPnLAggregator, RealizedNetPnLResult
from trading.session_net_pnl import SessionNetPnLAggregator, SessionNetPnLResult
from trading.unrealized_pnl import UnrealizedPnLAggregator, UnrealizedPnLResult


DAY = date(2026, 8, 24)
EXCHANGE_TIME = datetime(2026, 8, 24, 9, 15, tzinfo=EXCHANGE_TIMEZONE)


def closed(side=PositionSide.CE, entry=100.0, exit=105.0, exit_time=EXCHANGE_TIME):
    return ClosedPosition(side, f"NIFTY26AUG25000{side.value}", 65, entry,
                          exit_time - timedelta(minutes=30), exit, exit_time,
                          PositionState.CLOSED)


def active(side=PositionSide.CE, entry=100.0, entry_time=EXCHANGE_TIME):
    return ManagedPosition(side, f"NIFTY26AUG25000{side.value}", 65, entry,
                           entry_time, PositionState.OPEN)


def realized_aggregator(brokerage=20.0):
    schedule = OptionChargeSchedule(brokerage, .0015, .0003553, .000001, .00003, .18)
    return RealizedNetPnLAggregator(
        NetPnLCalculator(OptionTradeChargesCalculator(schedule))
    )


def aggregator(brokerage=20.0):
    return SessionNetPnLAggregator(realized_aggregator(brokerage))


def result(**overrides):
    values = dict(trading_date=DAY, closed_trade_count=2, open_position_count=1,
                  realized_gross_pnl=100.0, realized_total_charges=20.0,
                  realized_net_pnl=80.0, unrealized_gross_pnl=10.0,
                  session_net_pnl=90.0)
    values.update(overrides)
    return SessionNetPnLResult(**values)


@pytest.mark.parametrize("value", [None, object(), True, "aggregator"])
def test_constructor_requires_realized_net_aggregator(value):
    with pytest.raises(TypeError):
        SessionNetPnLAggregator(value)


@pytest.mark.parametrize("invalid", [None, "2026-08-24", datetime(2026, 8, 24), True])
def test_aggregate_requires_strict_date(invalid):
    with pytest.raises(TypeError):
        aggregator().aggregate(invalid, [])


def test_flat_session_returns_exact_zero_result():
    assert aggregator().aggregate(DAY, []) == SessionNetPnLResult(
        DAY, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0
    )


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
def test_matching_realized_trade_is_net_of_charges(side):
    summary = aggregator().aggregate(DAY, [closed(side)])
    assert summary.closed_trade_count == 1
    assert summary.open_position_count == 0
    assert summary.realized_total_charges > 0
    assert summary.session_net_pnl == summary.realized_net_pnl
    assert summary.unrealized_gross_pnl == 0.0


def test_mixed_matching_history_excludes_other_exchange_date():
    matching = [closed(PositionSide.CE), closed(PositionSide.PE)]
    excluded = closed(exit_time=EXCHANGE_TIME - timedelta(days=1))
    summary = aggregator().aggregate(DAY, [excluded, *matching])
    matching_only = aggregator().aggregate(DAY, matching)
    assert summary == matching_only
    assert summary.closed_trade_count == 2


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
@pytest.mark.parametrize("reference,sign", [(105, 1), (95, -1)])
def test_matching_active_position_contributes_gross_only(side, reference, sign):
    summary = aggregator().aggregate(DAY, [], active(side), reference)
    assert summary.closed_trade_count == 0
    assert summary.open_position_count == 1
    assert summary.realized_gross_pnl == summary.realized_total_charges == 0.0
    assert summary.realized_net_pnl == 0.0
    assert summary.unrealized_gross_pnl * sign > 0
    assert summary.session_net_pnl == summary.unrealized_gross_pnl


def test_out_of_session_active_rules():
    old = active(entry_time=EXCHANGE_TIME - timedelta(days=1))
    summary = aggregator().aggregate(DAY, [], old)
    assert summary.open_position_count == 0
    assert summary.unrealized_gross_pnl == 0.0
    with pytest.raises(ValueError):
        aggregator().aggregate(DAY, [], old, 105)


def test_matching_active_requires_reference_price():
    with pytest.raises(ValueError):
        aggregator().aggregate(DAY, [], active())


@pytest.mark.parametrize("exit_price,reference", [
    (110, 110), (110, 90), (90, 120), (90, 80), (100, 100)])
def test_combined_session_sign_combinations(exit_price, reference):
    summary = aggregator().aggregate(
        DAY, [closed(entry=100, exit=exit_price)], active(entry=100), reference
    )
    assert summary.session_net_pnl == fsum(
        (summary.realized_net_pnl, summary.unrealized_gross_pnl)
    )


def test_utc_exit_crossing_midnight_uses_exchange_date():
    timestamp = datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc)
    assert timestamp.date() != DAY
    assert timestamp.astimezone(EXCHANGE_TIMEZONE).date() == DAY
    assert aggregator().aggregate(DAY, [closed(exit_time=timestamp)]).closed_trade_count == 1
    assert aggregator().aggregate(date(2026, 8, 23), [closed(exit_time=timestamp)]).closed_trade_count == 0


def test_utc_active_entry_crossing_midnight_uses_exchange_date():
    timestamp = datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc)
    summary = aggregator().aggregate(DAY, [], active(entry_time=timestamp), 105)
    assert timestamp.date() != DAY
    assert summary.open_position_count == 1


def test_exchange_aware_nonmatching_timestamp_is_excluded():
    previous = datetime(2026, 8, 23, 23, 30, tzinfo=EXCHANGE_TIMEZONE)
    assert aggregator().aggregate(DAY, [closed(exit_time=previous)]).closed_trade_count == 0


def test_filtered_identities_and_order_counts_are_forwarded(monkeypatch):
    realized = realized_aggregator()
    matching = closed()
    excluded = closed(exit_time=EXCHANGE_TIME - timedelta(days=1))
    seen = []
    original = realized.aggregate
    def spy(positions, buy_count, sell_count):
        seen.append((positions, buy_count, sell_count))
        return original(positions, buy_count, sell_count)
    monkeypatch.setattr(realized, "aggregate", spy)
    SessionNetPnLAggregator(realized).aggregate(DAY, [excluded, matching],
                                                buy_order_count=2,
                                                sell_order_count=3)
    assert seen == [((matching,), 2, 3)]
    assert seen[0][0][0] is matching


def test_custom_counts_change_matching_realized_charges():
    positions = [closed()]
    normal = aggregator().aggregate(DAY, positions)
    custom = aggregator().aggregate(DAY, positions, buy_order_count=2,
                                    sell_order_count=4)
    assert custom.realized_total_charges > normal.realized_total_charges
    assert custom.realized_net_pnl < normal.realized_net_pnl


@pytest.mark.parametrize("count", [None, True, False, 0, -1, 1.5, "1"])
@pytest.mark.parametrize("argument", ["buy_order_count", "sell_order_count"])
def test_invalid_counts_fail_with_no_matching_trades(argument, count):
    with pytest.raises(ValueError):
        aggregator().aggregate(DAY, [], **{argument: count})


@pytest.mark.parametrize("positions", [None, {}, "positions", {1}, object(), [object()]])
def test_invalid_closed_history_rejected(positions):
    with pytest.raises(TypeError):
        aggregator().aggregate(DAY, positions)


@pytest.mark.parametrize("position", [object(), True, "position"])
def test_invalid_active_position_rejected(position):
    with pytest.raises(TypeError):
        aggregator().aggregate(DAY, [], position)


def test_tampered_non_open_active_rejected():
    position = active()
    object.__setattr__(position, "state", PositionState.CLOSED)
    with pytest.raises(ValueError):
        aggregator().aggregate(DAY, [], position, 105)


@pytest.mark.parametrize("price", [True, 0, -1, nan, inf, "105"])
def test_invalid_reference_price_rejected(price):
    with pytest.raises(ValueError):
        aggregator().aggregate(DAY, [], active(), price)


def test_exact_dependency_values_and_active_identity_consumed(monkeypatch):
    realized = realized_aggregator()
    position = active(PositionSide.PE)
    realized_value = RealizedNetPnLResult(1, 100, 20, 80, 1, 0, 0)
    unrealized_value = UnrealizedPnLResult(1, -30)
    active_seen = []
    monkeypatch.setattr(realized, "aggregate", lambda *args: realized_value)
    def unrealized_spy(self, supplied, reference):
        active_seen.append((supplied, reference))
        return unrealized_value
    monkeypatch.setattr(UnrealizedPnLAggregator, "aggregate", unrealized_spy)
    summary = SessionNetPnLAggregator(realized).aggregate(DAY, [closed()], position, 95)
    assert summary == SessionNetPnLResult(DAY, 1, 1, 100, 20, 80, -30, 50)
    assert active_seen == [(position, 95)]


def test_inputs_are_not_mutated():
    positions = [closed()]
    position = active()
    original = tuple(positions)
    aggregator().aggregate(DAY, positions, position, 105)
    assert tuple(positions) == original
    assert positions[0] is original[0]
    assert position.state is PositionState.OPEN


def test_result_is_frozen_with_exact_fields():
    value = result()
    assert [field.name for field in fields(value)] == [
        "trading_date", "closed_trade_count", "open_position_count",
        "realized_gross_pnl", "realized_total_charges", "realized_net_pnl",
        "unrealized_gross_pnl", "session_net_pnl"]
    with pytest.raises(FrozenInstanceError):
        value.session_net_pnl = 0


@pytest.mark.parametrize("invalid", [None, "2026-08-24", datetime(2026, 8, 24), True])
def test_result_requires_strict_date(invalid):
    with pytest.raises(TypeError):
        result(trading_date=invalid)


@pytest.mark.parametrize("field,bad", [
    ("closed_trade_count", -1), ("closed_trade_count", True),
    ("open_position_count", -1), ("open_position_count", 2),
    ("open_position_count", True)])
def test_result_rejects_invalid_counts(field, bad):
    with pytest.raises(ValueError):
        result(**{field: bad})


@pytest.mark.parametrize("field", ["realized_gross_pnl", "realized_total_charges",
    "realized_net_pnl", "unrealized_gross_pnl", "session_net_pnl"])
@pytest.mark.parametrize("bad", [True, nan, inf, -inf, object()])
def test_result_rejects_invalid_numbers(field, bad):
    with pytest.raises(ValueError):
        result(**{field: bad})


def test_result_rejects_negative_charges_and_inconsistent_totals():
    with pytest.raises(ValueError):
        result(realized_total_charges=-1)
    with pytest.raises(ValueError):
        result(realized_net_pnl=81)
    with pytest.raises(ValueError):
        result(session_net_pnl=91)


def test_source_is_pure_and_has_no_open_charge_or_clock_logic():
    source = Path("trading/session_net_pnl.py").read_text(encoding="utf-8").lower()
    forbidden = ("optioncharges", "optiontradecharges", "charge schedule",
                 "positionmanager", "positionstore", "brokerposition", "kite",
                 "zerodha", "marketdata", "websocket", "strategy", "readiness",
                 "authorization", "persistence", "network", "database", "today(",
                 "now(", "retry", "poll", "thread", "capital", "margin", "account",
                 "risk")
    assert all(term not in source for term in forbidden)
