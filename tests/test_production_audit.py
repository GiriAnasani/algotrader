from dataclasses import FrozenInstanceError, fields
from datetime import date, datetime, timezone
from enum import Enum
import json
import os

import pytest

from core.production_audit import (
    AuditEvent,
    AuditEventType,
    AuditWriteError,
    JsonLineAuditSink,
    sanitize_audit_data,
)


NOW = datetime(2026, 8, 25, 9, 15, tzinfo=timezone.utc)


def event(data=None, event_id="event-1"):
    return AuditEvent(
        event_id, AuditEventType.ORDER_SUBMISSION_ATTEMPTED,
        NOW, "correlation-1", data or {},
    )


def test_event_is_frozen_with_exact_schema_and_defensive_data_copy():
    source = {"quantity": 65}
    value = event(source)
    source["quantity"] = 1
    assert [item.name for item in fields(value)] == [
        "event_id", "event_type", "occurred_at", "correlation_id", "data"
    ]
    assert value.data["quantity"] == 65
    with pytest.raises(FrozenInstanceError):
        value.event_id = "changed"
    with pytest.raises(TypeError):
        value.data["quantity"] = 2


@pytest.mark.parametrize("value", [None, "", "  ", 1, True])
def test_event_id_must_be_non_empty_string(value):
    with pytest.raises((TypeError, ValueError)):
        event(event_id=value)


def test_event_requires_strict_type_and_aware_time():
    with pytest.raises(TypeError):
        AuditEvent("x", "ORDER", NOW, None, {})
    with pytest.raises(ValueError):
        AuditEvent("x", AuditEventType.STARTUP_CREATED, datetime(2026, 1, 1), None, {})


@pytest.mark.parametrize("value", ["", " ", 1, True])
def test_optional_correlation_id_is_strict(value):
    with pytest.raises(ValueError):
        AuditEvent("x", AuditEventType.STARTUP_CREATED, NOW, value, {})


@pytest.mark.parametrize(
    "key",
    ["api_secret", "kite_api_secret", "access_token", "request_token",
     "password", "pin", "totp", "authorization", "cookie", "session_token"],
)
def test_sensitive_keys_are_fully_redacted_case_insensitively(key):
    sanitized = sanitize_audit_data({key.upper(): "do-not-leak"})
    assert sanitized[key.upper()] == "***"
    assert "do-not-leak" not in event({key: "do-not-leak"}).to_json()


def test_nested_data_is_redacted_and_json_safe():
    value = event({
        "nested": {"Access_Token": "secret"},
        "at": NOW, "day": date(2026, 8, 25),
        "state": AuditEventType.RISK_REJECTED,
        "values": (1, True, None),
    })
    document = json.loads(value.to_json())
    assert document["data"]["nested"]["Access_Token"] == "***"
    assert document["data"]["at"] == NOW.isoformat()
    assert document["data"]["state"] == "RISK_REJECTED"


@pytest.mark.parametrize("value", [object(), b"secret", float("nan"), float("inf")])
def test_unsafe_values_are_rejected_without_repr(value):
    with pytest.raises((TypeError, ValueError)):
        event({"value": value})


def test_json_is_deterministic_compact_and_parseable():
    value = event({"z": 1, "a": 2})
    assert value.to_json() == value.to_json()
    assert " " not in value.to_json()
    assert json.loads(value.to_json())["event_id"] == "event-1"


def test_sink_is_lazy_append_only_and_one_line_per_event(tmp_path):
    path = tmp_path / "logs" / "audit.jsonl"
    sink = JsonLineAuditSink(path)
    assert not path.exists() and not path.parent.exists()
    sink.write(event({"number": 1}, "one"))
    first = path.read_bytes()
    sink.write(event({"number": 2}, "two"))
    assert path.read_bytes().startswith(first)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_id"] for line in lines] == ["one", "two"]
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_sink_preserves_existing_content(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text("existing\n", encoding="utf-8")
    JsonLineAuditSink(path).write(event())
    assert path.read_text(encoding="utf-8").startswith("existing\n")


def test_sink_write_failure_is_typed(tmp_path):
    path = tmp_path / "directory"
    path.mkdir()
    with pytest.raises(AuditWriteError):
        JsonLineAuditSink(path).write(event())
