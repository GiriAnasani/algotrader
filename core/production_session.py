"""Durable, locally eligible production authentication sessions."""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
import json
import os
from pathlib import Path
import tempfile

from trading.ohlc import EXCHANGE_TIMEZONE


class ProductionSessionStoreError(RuntimeError):
    """Raised when production session persistence cannot complete."""


class ProductionSessionStoreCorruptionError(ProductionSessionStoreError):
    """Raised when persisted production session data is malformed."""


def _aware_datetime(value, field_name):
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
    return value


@dataclass(frozen=True, repr=False)
class ProductionSession:
    """Sensitive access-token state with deterministic local eligibility."""

    access_token: str
    authenticated_at: datetime

    def __post_init__(self):
        if not isinstance(self.access_token, str):
            raise TypeError("access_token must be a string.")
        token = self.access_token.strip()
        if not token:
            raise ValueError("access_token must not be empty.")
        object.__setattr__(self, "access_token", token)
        _aware_datetime(self.authenticated_at, "authenticated_at")

    def __repr__(self):
        return (
            "ProductionSession(access_token='***', "
            f"authenticated_at={self.authenticated_at!r})"
        )

    __str__ = __repr__

    def is_eligible_at(self, now):
        """Returns local eligibility until the applicable next 06:00 IST."""
        _aware_datetime(now, "now")
        authenticated = self.authenticated_at.astimezone(EXCHANGE_TIMEZONE)
        evaluated = now.astimezone(EXCHANGE_TIMEZONE)
        boundary = datetime.combine(
            authenticated.date(), time(hour=6), tzinfo=EXCHANGE_TIMEZONE
        )
        if authenticated >= boundary:
            boundary += timedelta(days=1)
        return authenticated <= evaluated < boundary


class ProductionSessionStore:
    """Versioned atomic persistence for one sensitive production session."""

    VERSION = 1
    _FIELDS = {"version", "access_token", "authenticated_at"}

    def __init__(self, path):
        if isinstance(path, bool) or not isinstance(path, (str, os.PathLike)):
            raise TypeError("Session store path must be a filesystem path.")
        if isinstance(path, str) and not path.strip():
            raise ValueError("Session store path must not be empty.")
        self.path = Path(path)

    def save(self, session):
        if not isinstance(session, ProductionSession):
            raise TypeError("Session must be a ProductionSession.")
        document = {
            "version": self.VERSION,
            "access_token": session.access_token,
            "authenticated_at": session.authenticated_at.isoformat(),
        }
        temporary_path = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(document, temporary_file, sort_keys=True)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.path)
        except (OSError, TypeError, ValueError) as error:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise ProductionSessionStoreError(
                "Could not save the production session."
            ) from error

    def load(self):
        if not self.path.exists():
            return None
        try:
            with self.path.open("r", encoding="utf-8") as stored_file:
                document = json.load(stored_file)
            return self._deserialize(document)
        except (json.JSONDecodeError, UnicodeError, KeyError, TypeError, ValueError) as error:
            raise ProductionSessionStoreCorruptionError(
                "Persisted production session data is invalid."
            ) from error
        except OSError as error:
            raise ProductionSessionStoreError(
                "Could not read the production session."
            ) from error

    @classmethod
    def _deserialize(cls, document):
        if not isinstance(document, dict) or set(document) != cls._FIELDS:
            raise ValueError("Session document fields are invalid.")
        version = document["version"]
        if isinstance(version, bool) or not isinstance(version, int):
            raise TypeError("Session version must be an integer.")
        if version != cls.VERSION:
            raise ValueError("Session version is unsupported.")
        timestamp = document["authenticated_at"]
        if not isinstance(timestamp, str):
            raise TypeError("Session timestamp must be a string.")
        return ProductionSession(
            access_token=document["access_token"],
            authenticated_at=datetime.fromisoformat(timestamp),
        )
