from dataclasses import FrozenInstanceError, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from trading.close_position_lifecycle import ClosedPosition
from trading.ohlc import EXCHANGE_TIMEZONE
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.realized_pnl import RealizedPnLResult
from trading.session_pnl import SessionPnLAggregator, SessionPnLResult
from trading.unrealized_pnl import UnrealizedPnLResult


TRADING_DATE = date(2026, 8, 24)
ENTRY_TIME = datetime(2026, 8, 24, 9, 15, tzinfo=EXCHANGE_TIMEZONE)


def closed(side=PositionSide.CE, **overrides):
    values = {
        "side": side,
        "contract_symbol": f"NIFTY26AUG25000{side.value}",
        "quantity": 65,
        "entry_price": 100.0,
        "entry_time": ENTRY_TIME,
        "exit_price": 105.0,
        "exit_time": ENTRY_TIME + timedelta(minutes=1),
        "state": PositionState.CLOSED,
    }
    values.update(overrides)
    return ClosedPosition(**values)


def active(side=PositionSide.CE, **overrides):
    values = {
        "side": side,
        "contract_symbol": f"NIFTY26AUG25000{side.value}",
        "quantity": 65,
        "entry_price": 100.0,
        "entry_time": ENTRY_TIME,
        "state": PositionState.OPEN,
    }
    values.update(overrides)
    return ManagedPosition(**values)


@pytest.mark.parametrize("trading_date", [None, "2026-08-24", datetime(2026, 8, 24)])
def test_aggregate_requires_strict_date(trading_date):
    with pytest.raises(TypeError):
        SessionPnLAggregator().aggregate(trading_date, [])


def test_flat_session_returns_all_zero_values():
    result = SessionPnLAggregator().aggregate(TRADING_DATE, [])
    assert result == SessionPnLResult(TRADING_DATE, 0, 0, 0.0, 0.0, 0.0)


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
def test_matching_closed_trade_is_included_for_both_sides(side):
    result = SessionPnLAggregator().aggregate(TRADING_DATE, [closed(side)])
    assert result.closed_trade_count == 1
    assert result.realized_gross_pnl == 325.0
    assert result.open_position_count == 0


def test_nonmatching_closed_trades_are_excluded_from_mixed_history():
    positions = [
        closed(exit_price=105.0),
        closed(
            PositionSide.PE,
            exit_price=95.0,
            exit_time=ENTRY_TIME + timedelta(days=1),
        ),
        closed(
            exit_price=110.0,
            entry_time=ENTRY_TIME - timedelta(days=1, minutes=1),
            exit_time=ENTRY_TIME - timedelta(days=1),
        ),
    ]
    result = SessionPnLAggregator().aggregate(TRADING_DATE, positions)
    assert result.closed_trade_count == 1
    assert result.realized_gross_pnl == 325.0


def test_utc_exit_crossing_into_exchange_date_is_included():
    utc_exit = datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc)
    position = closed(
        entry_time=utc_exit - timedelta(minutes=1),
        exit_time=utc_exit,
    )
    result = SessionPnLAggregator().aggregate(TRADING_DATE, [position])
    assert utc_exit.date() == date(2026, 8, 23)
    assert utc_exit.astimezone(EXCHANGE_TIMEZONE).date() == TRADING_DATE
    assert result.closed_trade_count == 1


def test_utc_exit_on_raw_date_but_previous_exchange_session_is_excluded():
    utc_exit = datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc)
    position = closed(
        entry_time=utc_exit - timedelta(minutes=1),
        exit_time=utc_exit,
    )
    result = SessionPnLAggregator().aggregate(date(2026, 8, 23), [position])
    assert utc_exit.date() == TRADING_DATE
    assert utc_exit.astimezone(EXCHANGE_TIMEZONE).date() == TRADING_DATE
    assert result.closed_trade_count == 0


def test_exchange_aware_timestamp_is_classified_directly():
    result = SessionPnLAggregator().aggregate(
        TRADING_DATE,
        [closed(exit_time=datetime(2026, 8, 24, 23, 59, tzinfo=EXCHANGE_TIMEZONE))],
    )
    assert result.closed_trade_count == 1


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
@pytest.mark.parametrize(
    "reference_price, expected",
    [(105.0, 325.0), (95.0, -325.0), (100.0, 0.0)],
)
def test_matching_active_position_is_valued_for_both_sides(
    side, reference_price, expected
):
    result = SessionPnLAggregator().aggregate(
        TRADING_DATE, [], active(side), reference_price
    )
    assert result.open_position_count == 1
    assert result.unrealized_gross_pnl == expected


def test_nonmatching_active_position_is_excluded_without_price():
    position = active(entry_time=ENTRY_TIME - timedelta(days=1))
    result = SessionPnLAggregator().aggregate(TRADING_DATE, [], position)
    assert result.open_position_count == 0
    assert result.unrealized_gross_pnl == 0.0


def test_nonmatching_active_position_rejects_supplied_price():
    position = active(entry_time=ENTRY_TIME - timedelta(days=1))
    with pytest.raises(ValueError, match="reference price"):
        SessionPnLAggregator().aggregate(TRADING_DATE, [], position, 105.0)


def test_matching_active_position_requires_price():
    with pytest.raises(ValueError):
        SessionPnLAggregator().aggregate(TRADING_DATE, [], active())


@pytest.mark.parametrize(
    "closed_exit, reference_price, expected",
    [
        (105.0, 105.0, 650.0),
        (105.0, 95.0, 0.0),
        (95.0, 105.0, 0.0),
        (95.0, 95.0, -650.0),
    ],
)
def test_combined_session_adds_realized_and_unrealized(
    closed_exit, reference_price, expected
):
    result = SessionPnLAggregator().aggregate(
        TRADING_DATE,
        [closed(exit_price=closed_exit)],
        active(PositionSide.PE),
        reference_price,
    )
    assert result.total_gross_pnl == expected


@pytest.mark.parametrize("closed_positions", [None, {}, "positions", [object()]])
def test_invalid_closed_positions_are_rejected(closed_positions):
    with pytest.raises(TypeError):
        SessionPnLAggregator().aggregate(TRADING_DATE, closed_positions)


@pytest.mark.parametrize("active_position", [closed(), object(), "position", True, []])
def test_invalid_active_position_is_rejected(active_position):
    with pytest.raises(TypeError):
        SessionPnLAggregator().aggregate(
            TRADING_DATE, [], active_position, 105.0
        )


@pytest.mark.parametrize(
    "reference_price", [True, "105", 0, -1, float("nan"), float("inf"), float("-inf")]
)
def test_invalid_matching_reference_price_is_rejected(reference_price):
    with pytest.raises(ValueError):
        SessionPnLAggregator().aggregate(
            TRADING_DATE, [], active(), reference_price
        )


def test_result_is_frozen_and_has_exact_fields():
    result = SessionPnLResult(TRADING_DATE, 2, 1, 125, -50, 75)
    assert [field.name for field in fields(result)] == [
        "trading_date", "closed_trade_count", "open_position_count",
        "realized_gross_pnl", "unrealized_gross_pnl", "total_gross_pnl",
    ]
    assert result == SessionPnLResult(TRADING_DATE, 2, 1, 125.0, -50.0, 75.0)
    with pytest.raises(FrozenInstanceError):
        result.trading_date = date(2026, 8, 25)


@pytest.mark.parametrize(
    "values",
    [
        (None, 0, 0, 0.0, 0.0, 0.0),
        (datetime(2026, 8, 24), 0, 0, 0.0, 0.0, 0.0),
        (TRADING_DATE, -1, 0, 0.0, 0.0, 0.0),
        (TRADING_DATE, True, 0, 0.0, 0.0, 0.0),
        (TRADING_DATE, 0, -1, 0.0, 0.0, 0.0),
        (TRADING_DATE, 0, 2, 0.0, 0.0, 0.0),
        (TRADING_DATE, 0, True, 0.0, 0.0, 0.0),
        (TRADING_DATE, 0, 0, float("nan"), 0.0, 0.0),
        (TRADING_DATE, 0, 0, float("inf"), 0.0, 0.0),
        (TRADING_DATE, 0, 0, 0.0, float("nan"), 0.0),
        (TRADING_DATE, 0, 0, 0.0, float("inf"), 0.0),
        (TRADING_DATE, 0, 0, 0.0, 0.0, float("nan")),
        (TRADING_DATE, 0, 0, 0.0, 0.0, float("inf")),
        (TRADING_DATE, 0, 0, 10.0, 5.0, 14.0),
    ],
)
def test_result_rejects_invalid_invariants(values):
    with pytest.raises((TypeError, ValueError)):
        SessionPnLResult(*values)


def test_session_aggregator_reuses_component_aggregators(monkeypatch):
    closed_positions = [closed()]
    active_position = active()
    seen = []

    def fake_realized(self, positions):
        seen.append(("realized", positions))
        return RealizedPnLResult(1, 1, 0, 0, 125.0)

    def fake_unrealized(self, position, reference_price=None):
        seen.append(("unrealized", position, reference_price))
        return UnrealizedPnLResult(1, -50.0)

    monkeypatch.setattr(
        "trading.session_pnl.RealizedPnLAggregator.aggregate", fake_realized
    )
    monkeypatch.setattr(
        "trading.session_pnl.UnrealizedPnLAggregator.aggregate", fake_unrealized
    )
    result = SessionPnLAggregator().aggregate(
        TRADING_DATE, closed_positions, active_position, 99.0
    )
    assert seen[0][0] == "realized"
    assert seen[0][1] == tuple(closed_positions)
    assert seen[0][1][0] is closed_positions[0]
    assert seen[1] == ("unrealized", active_position, 99.0)
    assert seen[1][1] is active_position
    assert result == SessionPnLResult(TRADING_DATE, 1, 1, 125.0, -50.0, 75.0)


def test_input_positions_are_not_mutated():
    closed_position = closed()
    active_position = active()
    closed_values = tuple(closed_position.__dict__.values())
    active_values = tuple(active_position.__dict__.values())
    SessionPnLAggregator().aggregate(
        TRADING_DATE, [closed_position], active_position, 105.0
    )
    assert tuple(closed_position.__dict__.values()) == closed_values
    assert tuple(active_position.__dict__.values()) == active_values


def test_session_pnl_module_has_only_date_filter_and_calculation_dependencies():
    source = Path("trading/session_pnl.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "positionmanager", "positionstore", "brokerposition", "marketdata", "premium",
        "websocket", "strategy", "order", "readiness", "authorization", "persist",
        "database", "network", "date.today", "datetime.now", "retry", "poll", "thread",
        "sessionmanager", "current_session", "start_session", "end_session", "scheduler",
        "capital", "margin", "account", "brokerage", "tax", "fee", "charge", "net_pnl",
        "drawdown", "loss_limit", "kill_switch",
    )
    assert all(term not in source for term in forbidden)
