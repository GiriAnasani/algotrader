import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import trading.closed_position_history_store as store_module
from trading.closed_position_history import ClosedPositionHistory
from trading.closed_position_history_store import (
    ClosedPositionHistoryRestoreError,
    ClosedPositionHistoryStore,
    ClosedPositionHistoryStoreCorruptionError,
    ClosedPositionHistoryStoreError,
)
from trading.close_position_lifecycle import ClosedPosition
from trading.position import PositionSide, PositionState


NOW = datetime(2026, 8, 24, 9, 15, tzinfo=timezone.utc)


def closed(side=PositionSide.CE, entry=100, exit=105):
    return ClosedPosition(side, f"NIFTY26AUG25000{side.value}", 65, entry, NOW,
                          exit, NOW, PositionState.CLOSED)


def history(*positions):
    value = ClosedPositionHistory()
    for position in positions:
        value.record(position)
    return value


@pytest.mark.parametrize("path", [None, object(), True, ""])
def test_constructor_rejects_invalid_path(path):
    with pytest.raises((TypeError, ValueError)):
        ClosedPositionHistoryStore(path)


def test_missing_file_loads_empty_without_creation(tmp_path):
    store = ClosedPositionHistoryStore(tmp_path / "missing.json")
    assert store.load() == ()
    assert not store.path.exists()


def test_empty_history_saves_explicit_versioned_document(tmp_path):
    store = ClosedPositionHistoryStore(tmp_path / "nested" / "history.json")
    store.save(history())
    assert json.loads(store.path.read_text(encoding="utf-8")) == {
        "version": 1, "closed_positions": []}


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
def test_position_round_trip_preserves_exact_values_and_timezone(tmp_path, side):
    original = closed(side)
    store = ClosedPositionHistoryStore(tmp_path / "history.json")
    store.save(history(original))
    loaded = store.load()
    assert loaded == (original,)
    assert loaded[0] is not original
    assert loaded[0].side is side
    assert loaded[0].entry_time == NOW
    assert loaded[0].entry_time.tzinfo is not None
    assert loaded[0].state is PositionState.CLOSED


def test_multiple_positions_round_trip_in_order(tmp_path):
    positions = (closed(PositionSide.CE), closed(PositionSide.PE, 110, 90))
    store = ClosedPositionHistoryStore(tmp_path / "history.json")
    store.save(history(*positions))
    loaded = store.load()
    assert loaded == positions
    assert all(new is not old for new, old in zip(loaded, positions))


@pytest.mark.parametrize("document", [
    "not json", "{}", '{"closed_positions": []}',
    '{"version": 2, "closed_positions": []}',
    '{"version": true, "closed_positions": []}',
    '{"version": 1, "closed_positions": {}}'])
def test_corrupt_root_or_version_is_rejected(tmp_path, document):
    store = ClosedPositionHistoryStore(tmp_path / "history.json")
    store.path.write_text(document, encoding="utf-8")
    with pytest.raises(ClosedPositionHistoryStoreCorruptionError):
        store.load()


@pytest.mark.parametrize("field,bad", [
    ("side", "XX"), ("state", "OPEN"), ("quantity", 0),
    ("entry_price", -1), ("exit_price", -1),
    ("entry_time", "bad"), ("exit_time", "bad"),
    ("entry_time", "2026-08-24T09:15:00"),
    ("contract_symbol", "")])
def test_malformed_position_is_rejected(tmp_path, field, bad):
    store = ClosedPositionHistoryStore(tmp_path / "history.json")
    store.save(history(closed()))
    document = json.loads(store.path.read_text(encoding="utf-8"))
    document["closed_positions"][0][field] = bad
    store.path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ClosedPositionHistoryStoreCorruptionError):
        store.load()


def test_save_requires_history(tmp_path):
    with pytest.raises(TypeError):
        ClosedPositionHistoryStore(tmp_path / "history.json").save(object())


def test_save_replaces_from_same_directory(tmp_path, monkeypatch):
    store = ClosedPositionHistoryStore(tmp_path / "nested" / "history.json")
    seen = []
    original = store_module.os.replace
    def spy(source, target):
        seen.append((Path(source), Path(target)))
        return original(source, target)
    monkeypatch.setattr(store_module.os, "replace", spy)
    store.save(history(closed()))
    assert seen[0][0].parent == seen[0][1].parent == store.path.parent


def test_failed_replace_preserves_previous_file_and_cleans_temp(tmp_path, monkeypatch):
    store = ClosedPositionHistoryStore(tmp_path / "history.json")
    store.save(history(closed()))
    before = store.path.read_bytes()
    monkeypatch.setattr(store_module.os, "replace",
                        lambda *args: (_ for _ in ()).throw(OSError("failed")))
    with pytest.raises(ClosedPositionHistoryStoreError):
        store.save(history(closed(PositionSide.PE)))
    assert store.path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob(".*.tmp")) == []


def test_restore_records_exact_loaded_objects_in_order(tmp_path, monkeypatch):
    store = ClosedPositionHistoryStore(tmp_path / "history.json")
    store.save(history(closed(), closed(PositionSide.PE)))
    loaded = store.load()
    monkeypatch.setattr(store, "load", lambda: loaded)
    target = ClosedPositionHistory()
    returned = store.restore(target)
    assert returned is loaded
    assert target.positions == loaded
    assert all(left is right for left, right in zip(target.positions, loaded))


def test_restore_rejects_nonempty_target_without_loading(tmp_path, monkeypatch):
    store = ClosedPositionHistoryStore(tmp_path / "history.json")
    target = history(closed())
    monkeypatch.setattr(store, "load", lambda: pytest.fail("load called"))
    with pytest.raises(ClosedPositionHistoryRestoreError):
        store.restore(target)
