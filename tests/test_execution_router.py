from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trading.execution_mode import ExecutionMode
from trading.execution_router import ExecutionRouter
from trading.live_order import LiveOrderIntent
from trading.paper_execution import PaperExecutionEngine
from trading.paper_position import PositionStatus
from trading.strategy import SignalAction


TIME = datetime(2026, 8, 21, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))


def route(router, action, side="CE", **overrides):
    values = {
        "contract_symbol": "NIFTY2682125000CE",
        "side": side,
        "quantity": 75,
        "reference_price": 27.0,
        "created_time": TIME,
    }
    values.update(overrides)
    return router.route(action, **values)


def test_router_defaults_to_paper_mode():
    router = ExecutionRouter()

    assert router.mode is ExecutionMode.PAPER


def test_router_supports_explicit_live_intent_without_broker_transaction():
    router = ExecutionRouter(mode=ExecutionMode.LIVE)

    intent = route(router, SignalAction.BUY_CE)

    assert router.mode is ExecutionMode.LIVE
    assert isinstance(intent, LiveOrderIntent)
    assert intent.contract_symbol == "NIFTY2682125000CE"
    assert intent.reference_price == 27.0


def test_hold_creates_no_execution_or_live_intent():
    assert ExecutionRouter(mode=ExecutionMode.LIVE).route(SignalAction.HOLD) is None
    assert ExecutionRouter().route(SignalAction.HOLD) is None


def test_paper_router_uses_existing_paper_execution_engine():
    engine = PaperExecutionEngine()
    router = ExecutionRouter(paper_execution_engine=engine)

    opened = route(router, SignalAction.BUY_CE)
    closed = route(
        router,
        SignalAction.EXIT_CE,
        reference_price=30.0,
    )

    assert opened.status is PositionStatus.OPEN
    assert closed.status is PositionStatus.CLOSED
    assert engine.active_position is None


def test_router_does_not_silently_change_modes():
    router = ExecutionRouter(mode=ExecutionMode.LIVE)

    route(router, SignalAction.BUY_PE, side="PE")

    assert router.mode is ExecutionMode.LIVE


@pytest.mark.parametrize("mode", ["PAPER", None])
def test_router_requires_explicit_execution_mode(mode):
    with pytest.raises(TypeError, match="ExecutionMode"):
        ExecutionRouter(mode=mode)
