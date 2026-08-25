from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
import json
import os

import pytest

from core.production_session import (
    ProductionSession,
    ProductionSessionStore,
    ProductionSessionStoreCorruptionError,
    ProductionSessionStoreError,
)
from trading.ohlc import EXCHANGE_TIMEZONE


TOKEN = "fake-session-access-token"
AUTHENTICATED_AT = datetime(2026, 8, 25, 8, 0, tzinfo=EXCHANGE_TIMEZONE)


def test_session_is_frozen_with_exact_fields_and_safe_representation():
    session = ProductionSession(TOKEN, AUTHENTICATED_AT)
    assert [field.name for field in fields(session)] == [
        "access_token", "authenticated_at"
    ]
    assert TOKEN not in repr(session)
    assert TOKEN not in str(session)
    with pytest.raises(FrozenInstanceError):
        session.access_token = "replacement"


@pytest.mark.parametrize("token", [None, True, 1, object()])
def test_session_requires_string_access_token_without_echo(token):
    with pytest.raises(TypeError) as error:
        ProductionSession(token, AUTHENTICATED_AT)
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize("token", ["", "   ", "\t"])
def test_session_rejects_empty_access_token(token):
    with pytest.raises(ValueError):
        ProductionSession(token, AUTHENTICATED_AT)


@pytest.mark.parametrize("timestamp", [None, "time", object(), True])
def test_session_requires_datetime(timestamp):
    with pytest.raises(TypeError):
        ProductionSession(TOKEN, timestamp)


def test_session_requires_timezone_aware_authenticated_at():
    with pytest.raises(ValueError):
        ProductionSession(TOKEN, datetime(2026, 8, 25, 8, 0))


@pytest.mark.parametrize(
    "now, expected",
    [
        (datetime(2026, 8, 25, 15, 0, tzinfo=EXCHANGE_TIMEZONE), True),
        (datetime(2026, 8, 26, 5, 59, tzinfo=EXCHANGE_TIMEZONE), True),
        (datetime(2026, 8, 26, 6, 0, tzinfo=EXCHANGE_TIMEZONE), False),
        (datetime(2026, 8, 26, 7, 0, tzinfo=EXCHANGE_TIMEZONE), False),
        (datetime(2026, 8, 25, 2, 30, tzinfo=timezone.utc), True),
        (AUTHENTICATED_AT - timedelta(seconds=1), False),
    ],
)
def test_session_eligibility_uses_next_exchange_six_am(now, expected):
    assert ProductionSession(TOKEN, AUTHENTICATED_AT).is_eligible_at(now) is expected


def test_pre_six_authentication_uses_conservative_same_day_boundary():
    session = ProductionSession(
        TOKEN, datetime(2026, 8, 25, 5, 0, tzinfo=EXCHANGE_TIMEZONE)
    )
    assert session.is_eligible_at(
        datetime(2026, 8, 25, 5, 59, tzinfo=EXCHANGE_TIMEZONE)
    )
    assert not session.is_eligible_at(
        datetime(2026, 8, 25, 6, 0, tzinfo=EXCHANGE_TIMEZONE)
    )


def test_eligibility_rejects_naive_now():
    with pytest.raises(ValueError):
        ProductionSession(TOKEN, AUTHENTICATED_AT).is_eligible_at(
            datetime(2026, 8, 25, 15, 0)
        )


@pytest.mark.parametrize("path", [None, True, object(), ""])
def test_store_requires_filesystem_path(path):
    with pytest.raises((TypeError, ValueError)):
        ProductionSessionStore(path)


def test_missing_store_returns_none_without_creating_file(tmp_path):
    store = ProductionSessionStore(tmp_path / "session.json")
    assert store.load() is None
    assert not store.path.exists()


def test_store_round_trip_has_versioned_exact_schema(tmp_path):
    store = ProductionSessionStore(tmp_path / "nested" / "session.json")
    session = ProductionSession(TOKEN, AUTHENTICATED_AT)
    store.save(session)
    loaded = store.load()
    assert loaded == session
    document = json.loads(store.path.read_text(encoding="utf-8"))
    assert document == {
        "version": 1,
        "access_token": TOKEN,
        "authenticated_at": AUTHENTICATED_AT.isoformat(),
    }


@pytest.mark.parametrize(
    "document",
    [
        None,
        {},
        {"version": 2, "access_token": TOKEN, "authenticated_at": AUTHENTICATED_AT.isoformat()},
        {"version": 1, "access_token": "", "authenticated_at": AUTHENTICATED_AT.isoformat()},
        {"version": 1, "access_token": TOKEN, "authenticated_at": "invalid"},
        {"version": 1, "access_token": TOKEN, "authenticated_at": "2026-08-25T08:00:00"},
    ],
)
def test_corrupt_or_unsupported_session_document_is_rejected(tmp_path, document):
    store = ProductionSessionStore(tmp_path / "session.json")
    store.path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ProductionSessionStoreCorruptionError) as error:
        store.load()
    assert TOKEN not in str(error.value)


def test_malformed_json_is_rejected_without_token_echo(tmp_path):
    store = ProductionSessionStore(tmp_path / "session.json")
    store.path.write_text(TOKEN, encoding="utf-8")
    with pytest.raises(ProductionSessionStoreCorruptionError) as error:
        store.load()
    assert TOKEN not in str(error.value)


def test_atomic_replace_failure_preserves_previous_session(monkeypatch, tmp_path):
    store = ProductionSessionStore(tmp_path / "session.json")
    original = ProductionSession(TOKEN, AUTHENTICATED_AT)
    store.save(original)
    before = store.path.read_bytes()

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(ProductionSessionStoreError):
        store.save(ProductionSession("new-fake-token", AUTHENTICATED_AT))
    assert store.path.read_bytes() == before
    assert not list(tmp_path.glob(".*.tmp"))
