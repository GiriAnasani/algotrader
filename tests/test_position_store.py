from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

import trading.position_store as store_module
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import PositionManager
from trading.position_store import (
    PositionStore,
    PositionStoreCorruptionError,
    PositionStoreError,
)


ENTRY_TIME = datetime(
    2026, 8, 23, 9, 15, tzinfo=timezone(timedelta(hours=5, minutes=30))
)


def position(**overrides):
    values = {
        "side": PositionSide.CE,
        "contract_symbol": "NIFTY26AUG25000CE",
        "quantity": 65,
        "entry_price": 100.5,
        "entry_time": ENTRY_TIME,
        "state": PositionState.OPEN,
    }
    values.update(overrides)
    return ManagedPosition(**values)


def manager_with(active=None):
    manager = PositionManager()
    if active is not None:
        manager.register(active)
    return manager


def valid_document(**position_overrides):
    stored_position = {
        "side": "CE",
        "contract_symbol": "NIFTY26AUG25000CE",
        "quantity": 65,
        "entry_price": 100.5,
        "entry_time": "2026-08-23T09:15:00+05:30",
        "state": "OPEN",
    }
    stored_position.update(position_overrides)
    return {"version": 1, "active_position": stored_position}


def write_document(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")


@pytest.mark.parametrize("path_value", ["position.json", Path("position.json")])
def test_store_accepts_filesystem_paths(path_value):
    assert PositionStore(path_value).path == Path(path_value)


@pytest.mark.parametrize("path_value", [None, True, 1, object()])
def test_store_rejects_non_path_values(path_value):
    with pytest.raises(TypeError):
        PositionStore(path_value)


@pytest.mark.parametrize("path_value", ["", "   "])
def test_store_rejects_empty_string_path(path_value):
    with pytest.raises(ValueError):
        PositionStore(path_value)


def test_save_flat_writes_versioned_null_without_mutating_manager(tmp_path):
    path = tmp_path / "nested" / "position.json"
    manager = PositionManager()
    PositionStore(path).save(manager)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "active_position": None,
    }
    assert manager.active_position is None


def test_save_open_serializes_exact_values_without_mutation(tmp_path):
    path = tmp_path / "position.json"
    active = position()
    manager = manager_with(active)
    PositionStore(path).save(manager)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document == {
        "version": 1,
        "active_position": {
            "side": "CE",
            "contract_symbol": "NIFTY26AUG25000CE",
            "quantity": 65,
            "entry_price": 100.5,
            "entry_time": "2026-08-23T09:15:00+05:30",
            "state": "OPEN",
        },
    }
    assert manager.active_position is active
    assert active.entry_time is ENTRY_TIME


def test_load_flat_returns_none(tmp_path):
    path = tmp_path / "position.json"
    write_document(path, {"version": 1, "active_position": None})
    assert PositionStore(path).load() is None


def test_load_open_reconstructs_valid_new_managed_position(tmp_path):
    path = tmp_path / "position.json"
    write_document(path, valid_document())
    loaded = PositionStore(path).load()
    assert isinstance(loaded, ManagedPosition)
    assert loaded.side is PositionSide.CE
    assert loaded.contract_symbol == "NIFTY26AUG25000CE"
    assert loaded.quantity == 65
    assert loaded.entry_price == 100.5
    assert loaded.entry_time == ENTRY_TIME
    assert loaded.entry_time.utcoffset() == timedelta(hours=5, minutes=30)
    assert loaded.state is PositionState.OPEN


def test_open_and_flat_round_trips(tmp_path):
    path = tmp_path / "position.json"
    active = position()
    store = PositionStore(path)
    store.save(manager_with(active))
    loaded = store.load()
    assert loaded == active
    assert loaded is not active
    store.save(PositionManager())
    assert store.load() is None


def test_missing_file_returns_none_without_creating_file(tmp_path):
    path = tmp_path / "missing" / "position.json"
    assert PositionStore(path).load() is None
    assert not path.exists()
    assert not path.parent.exists()


def test_malformed_json_raises_corruption(tmp_path):
    path = tmp_path / "position.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(PositionStoreCorruptionError):
        PositionStore(path).load()


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"active_position": None},
        {"version": 2, "active_position": None},
        {"version": True, "active_position": None},
        {"version": 1, "active_position": []},
        {"version": 1, "active_position": "position"},
        {"version": 1, "active_position": {}, "extra": True},
        valid_document(side="CALL"),
        valid_document(contract_symbol=""),
        valid_document(contract_symbol="NIFTY26AUG25000PE"),
        valid_document(quantity=0),
        valid_document(quantity=-1),
        valid_document(quantity=True),
        valid_document(entry_price=-1),
        valid_document(entry_price="100.5"),
        valid_document(entry_time="not-a-time"),
        valid_document(entry_time="2026-08-23T09:15:00"),
        valid_document(state="CLOSED"),
        {"version": 1, "active_position": {**valid_document()["active_position"], "extra": 1}},
    ],
)
def test_invalid_schema_or_domain_values_raise_corruption(tmp_path, document):
    path = tmp_path / "position.json"
    write_document(path, document)
    with pytest.raises(PositionStoreCorruptionError):
        PositionStore(path).load()


def test_failed_atomic_replace_preserves_existing_file_and_manager(tmp_path, monkeypatch):
    path = tmp_path / "position.json"
    original = {"version": 1, "active_position": None}
    write_document(path, original)
    active = position()
    manager = manager_with(active)

    def fail_replace(source, destination):
        raise OSError("replacement failed")

    monkeypatch.setattr(store_module.os, "replace", fail_replace)
    with pytest.raises(PositionStoreError, match="Could not save"):
        PositionStore(path).save(manager)

    assert json.loads(path.read_text(encoding="utf-8")) == original
    assert manager.active_position is active
    assert list(tmp_path.glob("*.tmp")) == []


def test_store_uses_atomic_temporary_sibling_replacement():
    source = Path("trading/position_store.py").read_text(encoding="utf-8").lower()
    assert "namedtemporaryfile" in source
    assert "dir=self.path.parent" in source
    assert "os.replace" in source
    assert "flush()" in source
    assert "os.fsync" in source


def test_store_has_no_external_or_out_of_scope_dependencies():
    source = Path("trading/position_store.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "kiteconnect", "zerodha", "brokerposition", "brokerorderstatus",
        "marketdata", "liveexecutioncoordinator", "livereadinessgate",
        "positionrecoverycoordinator", "positionsafetyevaluator", "place_order",
        "cancel_order", "modify_order", "pickle", "sqlite", "database",
        "requests", "http", "datetime.now", "utcnow", "thread", "retry", "poll",
        "pnl", "portfolio",
    )
    assert all(term not in source for term in forbidden)
