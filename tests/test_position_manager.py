from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import (
    ActivePositionExistsError,
    NoActivePositionError,
    PositionManager,
    PositionManagerError,
)


ENTRY_TIME = datetime(2026, 8, 23, 9, 15, tzinfo=timezone.utc)


def make_position(side=PositionSide.CE, **overrides):
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


def test_new_manager_has_no_active_position():
    manager = PositionManager()
    assert manager.active_position is None
    assert manager.has_active_position is False


def test_register_retains_exact_position_reference_without_mutation():
    manager = PositionManager()
    position = make_position()
    original_values = tuple(position.__dict__.values())

    result = manager.register(position)

    assert result is None
    assert manager.active_position is position
    assert manager.has_active_position is True
    assert tuple(position.__dict__.values()) == original_values


@pytest.mark.parametrize("position", [None, object(), "position", True])
def test_register_rejects_non_managed_position(position):
    manager = PositionManager()
    with pytest.raises(TypeError):
        manager.register(position)
    assert manager.active_position is None


def test_register_rejects_closed_position():
    manager = PositionManager()
    with pytest.raises(ValueError):
        manager.register(make_position(state=PositionState.CLOSED))
    assert manager.active_position is None


@pytest.mark.parametrize(
    "replacement",
    [
        lambda active: active,
        lambda active: make_position(contract_symbol="NIFTY26SEP25000CE"),
        lambda active: make_position(PositionSide.PE),
    ],
)
def test_register_rejects_any_duplicate_or_conflicting_position(replacement):
    manager = PositionManager()
    active = make_position()
    manager.register(active)

    with pytest.raises(ActivePositionExistsError):
        manager.register(replacement(active))

    assert manager.active_position is active


def test_clear_returns_exact_position_and_removes_ownership_without_mutation():
    manager = PositionManager()
    position = make_position()
    manager.register(position)

    cleared = manager.clear()

    assert cleared is position
    assert cleared.state is PositionState.OPEN
    assert manager.active_position is None
    assert manager.has_active_position is False


def test_clear_without_active_position_is_rejected():
    manager = PositionManager()
    with pytest.raises(NoActivePositionError):
        manager.clear()


def test_manager_can_be_reused_after_clear_without_stale_position():
    manager = PositionManager()
    first = make_position()
    second = make_position(PositionSide.PE)

    manager.register(first)
    assert manager.clear() is first
    manager.register(second)

    assert manager.active_position is second
    assert manager.active_position is not first


def test_active_position_property_is_read_only_and_position_remains_frozen():
    manager = PositionManager()
    position = make_position()
    manager.register(position)

    with pytest.raises(AttributeError):
        manager.active_position = None
    with pytest.raises(FrozenInstanceError):
        manager.active_position.quantity = 130


def test_exception_hierarchy_is_small_and_domain_specific():
    assert issubclass(ActivePositionExistsError, PositionManagerError)
    assert issubclass(NoActivePositionError, PositionManagerError)


def test_manager_module_has_only_position_domain_dependency():
    source = Path("trading/position_manager.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "kiteconnect",
        "brokerposition",
        "brokerorderstatus",
        "zerodha",
        "marketdata",
        "liveexecutioncoordinator",
        "livereadinessgate",
        "executionrouter",
        "place_order",
        "profit",
        "loss",
        "portfolio",
        "synchroniz",
        "recovery",
        "restart",
        "reversal",
    )
    assert all(term not in source for term in forbidden)
