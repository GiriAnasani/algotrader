"""Versioned atomic persistence for LIVE closed-position history."""

from datetime import datetime
import json
import os
from pathlib import Path
import tempfile

from trading.closed_position_history import ClosedPositionHistory
from trading.close_position_lifecycle import ClosedPosition
from trading.position import PositionSide, PositionState


class ClosedPositionHistoryStoreError(RuntimeError):
    """Raised when durable closed-history persistence cannot complete."""


class ClosedPositionHistoryStoreCorruptionError(ClosedPositionHistoryStoreError):
    """Raised when durable closed-history data is malformed or unsupported."""


class ClosedPositionHistoryRestoreError(RuntimeError):
    """Raised when history restoration targets a non-empty runtime authority."""


class ClosedPositionHistoryStore:
    """Persist ordered history without becoming its runtime authority."""

    VERSION = 1
    _ROOT_FIELDS = {"version", "closed_positions"}
    _POSITION_FIELDS = {
        "side", "contract_symbol", "quantity", "entry_price", "entry_time",
        "exit_price", "exit_time", "state",
    }

    def __init__(self, path):
        if isinstance(path, bool) or not isinstance(path, (str, os.PathLike)):
            raise TypeError("Closed-position history path must be a filesystem path.")
        if isinstance(path, str) and not path.strip():
            raise ValueError("Closed-position history path must not be empty.")
        self.path = Path(path)

    def save(self, history):
        if not isinstance(history, ClosedPositionHistory):
            raise TypeError("History must be a ClosedPositionHistory.")
        document = {
            "version": self.VERSION,
            "closed_positions": [
                self._serialize(position) for position in history.positions
            ],
        }
        temporary_path = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(document, temporary_file, indent=2, sort_keys=True)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.path)
        except (OSError, TypeError, ValueError) as error:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise ClosedPositionHistoryStoreError(
                "Could not save closed-position history."
            ) from error

    def load(self):
        if not self.path.exists():
            return ()
        try:
            with self.path.open("r", encoding="utf-8") as stored_file:
                document = json.load(stored_file)
        except (json.JSONDecodeError, UnicodeError) as error:
            raise ClosedPositionHistoryStoreCorruptionError(
                "Persisted closed-position history JSON is malformed."
            ) from error
        except OSError as error:
            raise ClosedPositionHistoryStoreError(
                "Could not read closed-position history."
            ) from error
        try:
            return self._deserialize(document)
        except (KeyError, TypeError, ValueError) as error:
            raise ClosedPositionHistoryStoreCorruptionError(
                "Persisted closed-position history schema is invalid."
            ) from error

    def restore(self, history):
        if not isinstance(history, ClosedPositionHistory):
            raise TypeError("History must be a ClosedPositionHistory.")
        if history.positions:
            raise ClosedPositionHistoryRestoreError(
                "Closed-position history must be empty before restoration."
            )
        positions = self.load()
        for position in positions:
            history.record(position)
        return positions

    @staticmethod
    def _serialize(position):
        return {
            "side": position.side.value,
            "contract_symbol": position.contract_symbol,
            "quantity": position.quantity,
            "entry_price": position.entry_price,
            "entry_time": position.entry_time.isoformat(),
            "exit_price": position.exit_price,
            "exit_time": position.exit_time.isoformat(),
            "state": position.state.value,
        }

    @classmethod
    def _deserialize(cls, document):
        if not isinstance(document, dict) or set(document) != cls._ROOT_FIELDS:
            raise ValueError("History root must contain the exact versioned schema.")
        version = document["version"]
        if isinstance(version, bool) or not isinstance(version, int):
            raise TypeError("History version must be an integer.")
        if version != cls.VERSION:
            raise ValueError("History version is unsupported.")
        records = document["closed_positions"]
        if not isinstance(records, list):
            raise TypeError("Closed positions must be a list.")
        positions = []
        for record in records:
            if not isinstance(record, dict) or set(record) != cls._POSITION_FIELDS:
                raise ValueError("Closed-position fields do not match the schema.")
            side = PositionSide(record["side"])
            state = PositionState(record["state"])
            if state is not PositionState.CLOSED:
                raise ValueError("Persisted history positions must be CLOSED.")
            positions.append(ClosedPosition(
                side=side, contract_symbol=record["contract_symbol"],
                quantity=record["quantity"], entry_price=record["entry_price"],
                entry_time=datetime.fromisoformat(record["entry_time"]),
                exit_price=record["exit_price"],
                exit_time=datetime.fromisoformat(record["exit_time"]), state=state,
            ))
        return tuple(positions)
