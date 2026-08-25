from dataclasses import FrozenInstanceError, fields
from datetime import date, datetime, timedelta, timezone

import pytest

from trading.close_position_lifecycle import ClosedPosition
from trading.closed_position_history import ClosedPositionHistory
from trading.live_risk import (
    LiveRiskDecision,
    LiveRiskEvaluator,
    LiveRiskGuard,
    LiveRiskLimits,
    LiveRiskViolationError,
)
from trading.option_charges import (
    NetPnLCalculator,
    OptionChargeSchedule,
    OptionTradeChargesCalculator,
)
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import PositionManager
from trading.realized_net_pnl import RealizedNetPnLAggregator
from trading.session_net_pnl import SessionNetPnLAggregator
from trading.strategy import SignalAction


DAY = date(2026, 8, 24)
UTC_CROSSING = datetime(2026, 8, 23, 20, 0, tzinfo=timezone.utc)


def limits(**overrides):
    values = dict(max_open_positions=1, max_order_quantity=65,
                  max_completed_trades_per_day=2, max_realized_net_loss=1000.0)
    values.update(overrides)
    return LiveRiskLimits(**values)


def closed(exit_price=100.0, exit_time=UTC_CROSSING, entry_price=100.0):
    return ClosedPosition(
        PositionSide.CE, "NIFTY26AUG25000CE", 1, entry_price,
        exit_time - timedelta(minutes=1), exit_price, exit_time,
        PositionState.CLOSED,
    )


def evaluator(risk_limits=None, positions=()):
    manager = PositionManager()
    history = ClosedPositionHistory()
    for item in positions:
        history.record(item)
    zero_charges = OptionChargeSchedule(0, 0, 0, 0, 0, 0)
    aggregate = SessionNetPnLAggregator(
        RealizedNetPnLAggregator(
            NetPnLCalculator(OptionTradeChargesCalculator(zero_charges))
        )
    )
    return LiveRiskEvaluator(risk_limits or limits(), manager, history, aggregate)


def test_limits_and_decision_are_frozen_with_exact_fields():
    risk_limits = limits()
    assert [item.name for item in fields(risk_limits)] == [
        "max_open_positions", "max_order_quantity",
        "max_completed_trades_per_day", "max_realized_net_loss",
    ]
    with pytest.raises(FrozenInstanceError):
        risk_limits.max_order_quantity = 1
    decision = evaluator().evaluate(SignalAction.BUY_CE, 65, DAY)
    with pytest.raises(FrozenInstanceError):
        decision.allowed = False


@pytest.mark.parametrize("name", [
    "max_open_positions", "max_order_quantity", "max_completed_trades_per_day",
])
@pytest.mark.parametrize("value", [None, True, False, 0, -1, 1.0, "1"])
def test_integer_limits_are_strictly_positive(name, value):
    with pytest.raises(ValueError):
        limits(**{name: value})


@pytest.mark.parametrize("value", [None, True, False, 0, -1, float("inf"), float("nan")])
def test_realized_loss_limit_is_positive_finite(value):
    with pytest.raises(ValueError):
        limits(max_realized_net_loss=value)


def test_flat_buy_and_exact_quantity_boundary_are_allowed():
    decision = evaluator().evaluate(SignalAction.BUY_CE, 65, DAY)
    assert decision.allowed and decision.reason == "allowed"
    assert decision.open_position_count == 0


def test_quantity_over_max_is_blocked_and_guard_raises():
    decision = evaluator().evaluate(SignalAction.BUY_CE, 66, DAY)
    assert not decision.allowed and decision.reason == "max_order_quantity"
    with pytest.raises(LiveRiskViolationError, match="max_order_quantity"):
        LiveRiskGuard().validate(decision)


def test_active_position_blocks_new_entry_but_not_large_exit():
    risk = evaluator()
    active = ManagedPosition(
        PositionSide.CE, "NIFTY26AUG25000CE", 130, 100.0,
        UTC_CROSSING, PositionState.OPEN,
    )
    risk.position_manager.register(active)
    assert not risk.evaluate(SignalAction.BUY_PE, 65, DAY).allowed
    assert risk.evaluate(SignalAction.EXIT_CE, 130, DAY).allowed
    assert risk.position_manager.active_position is active


def test_completed_trade_limit_blocks_buy_at_equality_but_never_exit():
    risk = evaluator(positions=(closed(), closed(exit_price=101)))
    buy = risk.evaluate(SignalAction.BUY_CE, 65, DAY)
    exit_decision = risk.evaluate(SignalAction.EXIT_CE, 1000, DAY)
    assert buy.completed_trade_count == 2
    assert not buy.allowed and buy.reason == "max_completed_trades_per_day"
    assert exit_decision.allowed


@pytest.mark.parametrize(
    "realized, allowed",
    [(-999.99, True), (-1000.0, False), (-1200.0, False)],
)
def test_realized_net_loss_exact_boundary(realized, allowed):
    risk = evaluator(positions=(closed(exit_price=2000.0 + realized, entry_price=2000.0),))
    decision = risk.evaluate(SignalAction.BUY_CE, 65, DAY)
    assert decision.realized_net_pnl == realized
    assert decision.allowed is allowed
    if not allowed:
        assert decision.reason == "max_realized_net_loss"
    assert risk.evaluate(SignalAction.EXIT_CE, 1000, DAY).allowed


def test_exchange_local_date_filters_utc_crossing_closed_trade():
    item = closed(exit_price=50.0)
    assert item.exit_time.date() == date(2026, 8, 23)
    risk = evaluator(positions=(item,))
    decision = risk.evaluate(SignalAction.BUY_CE, 65, DAY)
    assert decision.completed_trade_count == 1
    assert decision.realized_net_pnl == -50.0
    assert risk.closed_position_history.positions[0] is item


@pytest.mark.parametrize("value", [None, datetime(2026, 8, 24), "2026-08-24"])
def test_trading_date_is_strict(value):
    with pytest.raises(TypeError):
        evaluator().evaluate(SignalAction.BUY_CE, 65, value)


@pytest.mark.parametrize("value", [None, True, 0, -1, 1.0])
def test_quantity_fails_closed(value):
    with pytest.raises(ValueError):
        evaluator().evaluate(SignalAction.BUY_CE, value, DAY)


def test_decision_rejects_invalid_enforcement_input():
    with pytest.raises(TypeError):
        LiveRiskGuard().validate(object())
