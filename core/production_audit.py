"""Immutable structured audit events and an append-only JSONL sink."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
import json
import math
import os
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4


class AuditEventType(Enum):
    STARTUP_CREATED = "STARTUP_CREATED"
    RISK_ALLOWED = "RISK_ALLOWED"
    RISK_REJECTED = "RISK_REJECTED"
    ORDER_INTENT_CREATED = "ORDER_INTENT_CREATED"
    ORDER_SUBMISSION_ATTEMPTED = "ORDER_SUBMISSION_ATTEMPTED"
    ORDER_SUBMISSION_CONFIRMED = "ORDER_SUBMISSION_CONFIRMED"
    ORDER_SUBMISSION_AMBIGUOUS = "ORDER_SUBMISSION_AMBIGUOUS"
    ORDER_STATUS_RECEIVED = "ORDER_STATUS_RECEIVED"
    ORDER_PENDING = "ORDER_PENDING"
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_CLOSED = "POSITION_CLOSED"


_SENSITIVE_KEYS = frozenset({
    "api_secret", "kite_api_secret", "access_token", "request_token",
    "password", "pin", "totp", "authorization", "cookie", "session_token",
})


def _safe_value(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Audit numbers must be finite.")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Audit datetimes must be timezone-aware.")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return _safe_value(value.value)
    if isinstance(value, Mapping):
        return sanitize_audit_data(value)
    if isinstance(value, (list, tuple)):
        return tuple(_safe_value(item) for item in value)
    raise TypeError("Audit data contains an unsupported value type.")


def sanitize_audit_data(data):
    """Return an immutable, JSON-safe copy with sensitive values redacted."""
    if not isinstance(data, Mapping):
        raise TypeError("Audit data must be a mapping.")
    sanitized = {}
    for key, value in data.items():
        if not isinstance(key, str):
            raise TypeError("Audit data keys must be strings.")
        sanitized[key] = (
            "***" if key.casefold() in _SENSITIVE_KEYS else _safe_value(value)
        )
    return MappingProxyType(sanitized)


def _plain_json_value(value):
    if isinstance(value, Mapping):
        return {key: _plain_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    event_type: AuditEventType
    occurred_at: datetime
    correlation_id: str | None
    data: Mapping

    def __post_init__(self):
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("Event ID must be a non-empty string.")
        if not isinstance(self.event_type, AuditEventType):
            raise TypeError("Event type must be an AuditEventType.")
        if not isinstance(self.occurred_at, datetime):
            raise TypeError("Occurred at must be a datetime.")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("Occurred at must be timezone-aware.")
        if self.correlation_id is not None and (
            not isinstance(self.correlation_id, str)
            or not self.correlation_id.strip()
        ):
            raise ValueError("Correlation ID must be a non-empty string or None.")
        object.__setattr__(self, "event_id", self.event_id.strip())
        if self.correlation_id is not None:
            object.__setattr__(self, "correlation_id", self.correlation_id.strip())
        object.__setattr__(self, "data", sanitize_audit_data(self.data))

    @classmethod
    def create(cls, event_type, occurred_at, data=None, correlation_id=None):
        return cls(uuid4().hex, event_type, occurred_at, correlation_id, data or {})

    def to_dict(self):
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "occurred_at": self.occurred_at.isoformat(),
            "correlation_id": self.correlation_id,
            "data": _plain_json_value(self.data),
        }

    def to_json(self):
        return json.dumps(
            self.to_dict(), sort_keys=True, allow_nan=False,
            separators=(",", ":"), ensure_ascii=False,
        )


class AuditWriteError(RuntimeError):
    """Raised when an audit record cannot be durably appended."""


class AuditSink(ABC):
    @abstractmethod
    def write(self, event):
        """Durably record one event."""


class JsonLineAuditSink(AuditSink):
    """Single-process append-only durable JSON Lines audit sink."""

    def __init__(self, path):
        if not isinstance(path, (str, Path)) or not str(path):
            raise TypeError("Audit path must be a path-like string or Path.")
        self.path = Path(path)

    def write(self, event):
        if not isinstance(event, AuditEvent):
            raise TypeError("Event must be an AuditEvent.")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(event.to_json() + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            if os.name != "nt":
                os.chmod(self.path, 0o600)
        except (OSError, TypeError, ValueError) as error:
            raise AuditWriteError("Could not durably append audit event.") from error
        return event
