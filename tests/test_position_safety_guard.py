from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading.broker_position import BrokerPosition
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import PositionManager
from trading.position_recovery import PositionRecoveryCoordinator
from trading.position_safety import PositionSafetyEvaluator, PositionSafetyState
from trading.position_safety_guard import (
    PositionSafetyGuard,
    PositionSafetyViolationError,
)
from trading.strategy import SignalAction


SYMBOL_CE = "NIFTY26AUG25000CE"
SYMBOL_PE = "NIFTY26AUG25000PE"


def managed(side=PositionSide.CE, symbol=SYMBOL_CE, quantity=65):
    return ManagedPosition(
        side=side,
        contract_symbol=symbol,
        quantity=quantity,
        entry_price=100.0,
        entry_time=datetime(2026, 8, 23, 9, 15, tzinfo=timezone.utc),
        state=PositionState.OPEN,
    )


def broker(symbol=SYMBOL_CE, quantity=65):
    return BrokerPosition(symbol, "NFO", quantity, 101.0, "MIS")


def safety(manager=None, positions=()):
    manager = manager or PositionManager()
    recovery = PositionRecoveryCoordinator(manager).recover(positions)
    return PositionSafetyEvaluator().evaluate(recovery)


@pytest.mark.parametrize("action", [SignalAction.BUY_CE, SignalAction.BUY_PE])
def test_buy_is_allowed_only_when_safely_flat(action):
    diagnostic = safety()
    assert diagnostic.state is PositionSafetyState.SAFE_FLAT
    assert PositionSafetyGuard().validate(action, diagnostic, SYMBOL_CE, 65) is None


@pytest.mark.parametrize("action", [SignalAction.BUY_CE, SignalAction.BUY_PE])
@pytest.mark.parametrize(
    "diagnostic",
    [
        pytest.param(
            (lambda m: (m.register(managed()), safety(m, [broker()]))[1])(
                PositionManager()
            ),
            id="safe-open",
        ),
        pytest.param(safety(positions=[broker()]), id="adoption-required"),
        pytest.param(
            (lambda m: (m.register(managed()), safety(m))[1])(PositionManager()),
            id="stale-managed",
        ),
        pytest.param(
            safety(positions=[broker(), broker(SYMBOL_PE, 50)]),
            id="ambiguous",
        ),
    ],
)
def test_non_flat_safety_blocks_buy_with_exact_diagnostic(action, diagnostic):
    with pytest.raises(PositionSafetyViolationError) as captured:
        PositionSafetyGuard().validate(action, diagnostic, SYMBOL_CE, 65)
    assert captured.value.action is action
    assert captured.value.safety_result is diagnostic


@pytest.mark.parametrize(
    ("action", "side", "symbol", "quantity"),
    [
        (SignalAction.EXIT_CE, PositionSide.CE, SYMBOL_CE, 65),
        (SignalAction.EXIT_PE, PositionSide.PE, SYMBOL_PE, 50),
    ],
)
def test_matching_exit_is_allowed_only_when_safely_open(
    action, side, symbol, quantity
):
    manager = PositionManager()
    manager.register(managed(side, symbol, quantity))
    diagnostic = safety(manager, [broker(symbol, quantity)])
    assert diagnostic.state is PositionSafetyState.SAFE_OPEN
    assert PositionSafetyGuard().validate(
        action, diagnostic, symbol, quantity
    ) is None


@pytest.mark.parametrize(
    "diagnostic",
    [
        safety(),
        safety(positions=[broker()]),
        (lambda m: (m.register(managed()), safety(m))[1])(PositionManager()),
        safety(positions=[broker(), broker(SYMBOL_PE, 50)]),
    ],
)
def test_non_open_safety_blocks_exit(diagnostic):
    with pytest.raises(PositionSafetyViolationError):
        PositionSafetyGuard().validate(
            SignalAction.EXIT_CE, diagnostic, SYMBOL_CE, 65
        )


@pytest.mark.parametrize(
    ("action", "symbol", "quantity"),
    [
        (SignalAction.EXIT_PE, SYMBOL_CE, 65),
        (SignalAction.EXIT_CE, "NIFTY26AUG25100CE", 65),
        (SignalAction.EXIT_CE, SYMBOL_CE, 130),
    ],
)
def test_safe_open_exit_must_match_side_symbol_and_quantity(action, symbol, quantity):
    manager = PositionManager()
    active = managed()
    manager.register(active)
    diagnostic = safety(manager, [broker()])
    with pytest.raises(PositionSafetyViolationError):
        PositionSafetyGuard().validate(action, diagnostic, symbol, quantity)
    assert manager.active_position is active


@pytest.mark.parametrize("action", [None, "BUY_CE", True, SignalAction.HOLD])
def test_guard_rejects_invalid_or_unsupported_action(action):
    with pytest.raises(TypeError):
        PositionSafetyGuard().validate(action, safety(), SYMBOL_CE, 65)


@pytest.mark.parametrize("diagnostic", [None, object(), "safe", True])
def test_guard_accepts_only_trusted_safety_result(diagnostic):
    with pytest.raises(TypeError):
        PositionSafetyGuard().validate(
            SignalAction.BUY_CE, diagnostic, SYMBOL_CE, 65
        )


def test_guard_module_has_no_broker_manager_or_readiness_dependency():
    source = Path("trading/position_safety_guard.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "kiteconnect", "zerodha", "brokerposition", "positionmanager",
        "livereadinessgate", ".register(", ".clear(", "positions(",
        "place_order", "cancel_order", "modify_order",
    )
    assert all(term not in source for term in forbidden)
