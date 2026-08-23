"""Filesystem persistence for last-known managed position state."""

from datetime import datetime
import json
import os
from pathlib import Path
import tempfile

from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import PositionManager


class PositionStoreError(RuntimeError):
    """Raised when managed-position persistence cannot complete."""


class PositionStoreCorruptionError(PositionStoreError):
    """Raised when persisted managed-position data is malformed or unsupported."""


class PositionStore:
    """Persist and reconstruct last-known managed state without adopting it."""

    VERSION = 1
    _ROOT_FIELDS = {"version", "active_position"}
    _POSITION_FIELDS = {
        "side",
        "contract_symbol",
        "quantity",
        "entry_price",
        "entry_time",
        "state",
    }

    def __init__(self, path):
        if isinstance(path, bool) or not isinstance(path, (str, os.PathLike)):
            raise TypeError("Position store path must be a filesystem path.")
        if isinstance(path, str) and not path.strip():
            raise ValueError("Position store path must not be empty.")
        self.path = Path(path)

    def save(self, position_manager):
        """Atomically persist the manager's current open position or flat state."""
        if not isinstance(position_manager, PositionManager):
            raise TypeError("Position manager must be a PositionManager.")

        position = position_manager.active_position
        if position is not None:
            if not isinstance(position, ManagedPosition):
                raise PositionStoreError("Active position must be a ManagedPosition.")
            if position.state is not PositionState.OPEN:
                raise PositionStoreError("Only an OPEN managed position may be saved.")

        document = {
            "version": self.VERSION,
            "active_position": self._serialize_position(position),
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
            raise PositionStoreError("Could not save managed position state.") from error

    def load(self):
        """Return reconstructed local state, or None when missing or persisted flat."""
        if not self.path.exists():
            return None
        try:
            with self.path.open("r", encoding="utf-8") as stored_file:
                document = json.load(stored_file)
        except (json.JSONDecodeError, UnicodeError) as error:
            raise PositionStoreCorruptionError(
                "Persisted managed position JSON is malformed."
            ) from error
        except OSError as error:
            raise PositionStoreError("Could not read managed position state.") from error

        try:
            return self._deserialize_document(document)
        except (KeyError, TypeError, ValueError) as error:
            raise PositionStoreCorruptionError(
                "Persisted managed position schema is invalid."
            ) from error

    @staticmethod
    def _serialize_position(position):
        if position is None:
            return None
        return {
            "side": position.side.value,
            "contract_symbol": position.contract_symbol,
            "quantity": position.quantity,
            "entry_price": position.entry_price,
            "entry_time": position.entry_time.isoformat(),
            "state": position.state.value,
        }

    @classmethod
    def _deserialize_document(cls, document):
        if not isinstance(document, dict) or set(document) != cls._ROOT_FIELDS:
            raise ValueError("Store root must contain the exact versioned schema.")
        version = document["version"]
        if isinstance(version, bool) or not isinstance(version, int):
            raise TypeError("Store version must be an integer.")
        if version != cls.VERSION:
            raise ValueError("Store version is unsupported.")

        stored_position = document["active_position"]
        if stored_position is None:
            return None
        if not isinstance(stored_position, dict):
            raise TypeError("Active position must be an object or null.")
        if set(stored_position) != cls._POSITION_FIELDS:
            raise ValueError("Active position fields do not match the schema.")

        side = PositionSide(stored_position["side"])
        state = PositionState(stored_position["state"])
        if state is not PositionState.OPEN:
            raise ValueError("Persisted active position must be OPEN.")
        entry_time = datetime.fromisoformat(stored_position["entry_time"])
        return ManagedPosition(
            side=side,
            contract_symbol=stored_position["contract_symbol"],
            quantity=stored_position["quantity"],
            entry_price=stored_position["entry_price"],
            entry_time=entry_time,
            state=state,
        )
