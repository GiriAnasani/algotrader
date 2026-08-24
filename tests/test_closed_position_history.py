from datetime import datetime, timezone

import pytest

from trading.closed_position_history import (
    ClosedPositionHistory,
    DuplicateClosedPositionError,
)
from trading.close_position_lifecycle import ClosedPosition
from trading.position import PositionSide, PositionState


NOW = datetime(2026, 8, 24, 9, 15, tzinfo=timezone.utc)


def closed(side=PositionSide.CE):
    return ClosedPosition(side, f"NIFTY26AUG25000{side.value}", 65, 100, NOW,
                          105, NOW, PositionState.CLOSED)


def test_history_starts_empty_and_exposes_immutable_tuple():
    history = ClosedPositionHistory()
    assert history.positions == ()
    assert isinstance(history.positions, tuple)


def test_record_preserves_exact_identity_and_insertion_order():
    history = ClosedPositionHistory()
    first = closed(PositionSide.CE)
    second = closed(PositionSide.PE)
    assert history.record(first) is first
    history.record(second)
    assert history.positions == (first, second)
    assert history.positions[0] is first
    assert history.positions[1] is second


@pytest.mark.parametrize("value", [None, object(), True, "position"])
def test_record_rejects_invalid_values(value):
    with pytest.raises(TypeError):
        ClosedPositionHistory().record(value)


def test_same_exact_object_is_rejected_as_duplicate():
    history = ClosedPositionHistory()
    position = closed()
    history.record(position)
    with pytest.raises(DuplicateClosedPositionError):
        history.record(position)
    assert history.positions == (position,)


def test_distinct_equal_positions_are_distinct_trades():
    history = ClosedPositionHistory()
    first = closed()
    second = closed()
    assert first == second and first is not second
    history.record(first)
    history.record(second)
    assert history.positions == (first, second)


def test_returned_tuple_cannot_mutate_internal_history():
    history = ClosedPositionHistory()
    first = closed()
    history.record(first)
    snapshot = history.positions
    snapshot += (closed(PositionSide.PE),)
    assert history.positions == (first,)


def test_history_has_no_removal_or_clear_api():
    history = ClosedPositionHistory()
    assert not hasattr(history, "clear")
    assert not hasattr(history, "remove")
    assert not hasattr(history, "pop")
