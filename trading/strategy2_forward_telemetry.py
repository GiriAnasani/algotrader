"""Durable forward-validation telemetry for frozen Strategy 2."""

import csv
from datetime import datetime
import json
import math
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo


STRATEGY_ID = "EXP-STRAT-02-FROZEN-V1"
IST = ZoneInfo("Asia/Kolkata")
ENTRY_STATUSES = {
    "ENTRY_PENDING", "SHADOW_POSITION_ACTIVE",
    "MISSED_EXPECTED_ENTRY_MINUTE", "CLOSED",
}
EXIT_REASONS = {"", "TARGET", "STOP"}
NEGATIVE_SUBSCRIPTION_LATENCY = (
    "NEGATIVE_CONFIRMATION_TO_SUBSCRIPTION_LATENCY"
)
NEGATIVE_ENTRY_LATENCY = "NEGATIVE_CONFIRMATION_TO_ENTRY_OBSERVATION_LATENCY"
SUBSCRIPTION_FAILED = "SUBSCRIPTION_FAILED"


class Strategy2TelemetryStoreError(RuntimeError):
    """Base error for Strategy 2 telemetry persistence."""


class Strategy2TelemetryLedgerError(Strategy2TelemetryStoreError):
    """Raised when the authoritative CSV cannot be read or written."""


class Strategy2TelemetrySummaryError(Strategy2TelemetryStoreError):
    """Raised when the derived summary cache cannot be written."""


class Strategy2ForwardTelemetryStore:
    """Authoritative atomic CSV ledger plus reconstructable summary cache."""

    COLUMNS = (
        "record_id", "strategy_id", "session_date", "direction",
        "qualification_time", "pullback_time", "confirmation_time",
        "spot_at_confirmation", "atm_strike", "otm_strike",
        "contract_symbol", "instrument_token", "expected_entry_time",
        "theoretical_entry_time", "theoretical_entry_price", "target_price",
        "stop_price", "exit_time", "exit_price", "exit_reason",
        "entry_status", "missed_entry_reason",
        "confirmation_to_subscription_latency_ms",
        "confirmation_to_entry_observation_latency_ms", "data_quality_flags",
    )
    SUMMARY_FIELDS = (
        "strategy_id", "total_confirmations", "theoretical_entries",
        "missed_entries", "closed_trades", "targets", "stops",
        "open_shadow_positions", "last_updated",
    )

    def __init__(self, root=None, clock=None):
        if root is None:
            root = Path(os.getenv("JARVIS_STATE_DIR", ".")) / "strategy2_forward"
        self.root = Path(root)
        self.ledger_path = self.root / f"{STRATEGY_ID}_forward_trades.csv"
        self.summary_path = self.root / f"{STRATEGY_ID}_forward_summary.json"
        self._clock = clock or (lambda: datetime.now(IST))
        self._records = self._load_records()
        self._ensure_root()
        self._write_summary()

    @staticmethod
    def lifecycle_id(confirmation_time, instrument_token, direction):
        confirmation_time = _aware_time(confirmation_time, "confirmation_time")
        direction = _direction(direction)
        token = _token(instrument_token)
        canonical_time = confirmation_time.astimezone(IST).isoformat()
        return f"{STRATEGY_ID}|{canonical_time}|{token}|{direction}"

    @property
    def records(self):
        return tuple(dict(record) for record in self._records.values())

    def get_lifecycle(self, record_id):
        """Return a copy of the authoritative lifecycle state."""
        return dict(self._required(record_id))

    def record_confirmation(
        self, *, direction, qualification_time, pullback_time,
        confirmation_time, spot_at_confirmation, contract,
        expected_entry_time, data_quality_flags=None,
    ):
        confirmation_time = _aware_time(confirmation_time, "confirmation_time")
        record_id = self.lifecycle_id(
            confirmation_time, contract["instrument_token"], direction
        )
        if record_id in self._records:
            self._write_summary()
            return record_id
        record = {column: "" for column in self.COLUMNS}
        record.update({
            "record_id": record_id,
            "strategy_id": STRATEGY_ID,
            "session_date": confirmation_time.astimezone(IST).date().isoformat(),
            "direction": _direction(direction),
            "qualification_time": _optional_time(qualification_time),
            "pullback_time": _optional_time(pullback_time),
            "confirmation_time": confirmation_time.astimezone(IST).isoformat(),
            "spot_at_confirmation": _price(
                spot_at_confirmation, "spot_at_confirmation"
            ),
            "atm_strike": _price(contract["atm_strike"], "atm_strike"),
            "otm_strike": _price(contract["otm_strike"], "otm_strike"),
            "contract_symbol": _nonempty(
                contract["tradingsymbol"], "contract_symbol"
            ),
            "instrument_token": _token(contract["instrument_token"]),
            "expected_entry_time": _aware_time(
                expected_entry_time, "expected_entry_time"
            ).astimezone(IST).isoformat(),
            "entry_status": "ENTRY_PENDING",
            "data_quality_flags": _serialize_flags(data_quality_flags),
        })
        self._commit_new(record_id, record)
        return record_id

    def record_subscription(self, record_id, observed_at):
        record = self._required(record_id)
        observed_at = _aware_time(observed_at, "subscription_observed_at")
        confirmation_time = datetime.fromisoformat(record["confirmation_time"])
        latency = (observed_at - confirmation_time).total_seconds() * 1000.0
        flags = _deserialize_flags(record["data_quality_flags"])
        if latency < 0:
            latency_value = ""
            flags.add(NEGATIVE_SUBSCRIPTION_LATENCY)
        else:
            latency_value = latency
        self._commit_update(record, {
            "confirmation_to_subscription_latency_ms": latency_value,
            "data_quality_flags": _serialize_flags(flags),
        })

    def record_data_quality_flag(self, record_id, flag):
        record = self._required(record_id)
        flags = _deserialize_flags(record["data_quality_flags"])
        flags.update(_normalize_flags([flag]))
        self._commit_update(
            record, {"data_quality_flags": _serialize_flags(flags)}
        )

    def record_theoretical_entry(
        self, record_id, *, entry_time, entry_price, target_price, stop_price
    ):
        record = self._required(record_id)
        if record["theoretical_entry_time"]:
            self._write_summary()
            return dict(record)
        entry_time = _aware_time(entry_time, "entry_time")
        confirmation_time = datetime.fromisoformat(record["confirmation_time"])
        latency = (entry_time - confirmation_time).total_seconds() * 1000.0
        flags = _deserialize_flags(record["data_quality_flags"])
        if latency < 0:
            latency_value = ""
            flags.add(NEGATIVE_ENTRY_LATENCY)
        else:
            latency_value = latency
        self._commit_update(record, {
            "theoretical_entry_time": entry_time.astimezone(IST).isoformat(),
            "theoretical_entry_price": _price(entry_price, "entry_price"),
            "target_price": _price(target_price, "target_price"),
            "stop_price": _price(stop_price, "stop_price"),
            "entry_status": "SHADOW_POSITION_ACTIVE",
            "confirmation_to_entry_observation_latency_ms": latency_value,
            "data_quality_flags": _serialize_flags(flags),
        })
        return dict(record)

    def record_missed_entry(self, record_id, reason):
        record = self._required(record_id)
        if record["theoretical_entry_time"] or record["missed_entry_reason"]:
            self._write_summary()
            return
        self._commit_update(record, {
            "entry_status": "MISSED_EXPECTED_ENTRY_MINUTE",
            "missed_entry_reason": _nonempty(reason, "missed_entry_reason"),
        })

    def record_exit(self, record_id, *, exit_time, exit_price, exit_reason):
        record = self._required(record_id)
        if record["exit_time"]:
            self._write_summary()
            return dict(record)
        if not record["theoretical_entry_time"]:
            raise Strategy2TelemetryStoreError(
                "Cannot close Strategy 2 telemetry without an entry."
            )
        if exit_reason not in ("TARGET", "STOP"):
            raise Strategy2TelemetryStoreError("Invalid Strategy 2 exit reason.")
        self._commit_update(record, {
            "exit_time": _aware_time(exit_time, "exit_time").astimezone(
                IST
            ).isoformat(),
            "exit_price": _price(exit_price, "exit_price"),
            "exit_reason": exit_reason,
            "entry_status": "CLOSED",
        })
        return dict(record)

    def _commit_new(self, record_id, record):
        self._records[record_id] = record
        try:
            self._write_ledger()
        except Strategy2TelemetryLedgerError:
            self._records.pop(record_id, None)
            raise
        self._write_summary()

    def _commit_update(self, record, updates):
        previous = dict(record)
        record.update(updates)
        try:
            self._write_ledger()
        except Strategy2TelemetryLedgerError:
            record.clear()
            record.update(previous)
            raise
        self._write_summary()

    def _required(self, record_id):
        try:
            return self._records[record_id]
        except KeyError as error:
            raise Strategy2TelemetryStoreError(
                "Strategy 2 telemetry lifecycle does not exist."
            ) from error

    def _load_records(self):
        if not self.ledger_path.exists():
            return {}
        try:
            with self.ledger_path.open("r", encoding="utf-8", newline="") as source:
                reader = csv.DictReader(source)
                if tuple(reader.fieldnames or ()) != self.COLUMNS:
                    raise Strategy2TelemetryLedgerError(
                        "Strategy 2 telemetry CSV schema is invalid."
                    )
                records = {}
                for record in reader:
                    self._validate_loaded_record(record)
                    record_id = record["record_id"]
                    if record_id in records:
                        raise Strategy2TelemetryLedgerError(
                            "Strategy 2 telemetry contains duplicate identities."
                        )
                    records[record_id] = record
                return records
        except Strategy2TelemetryLedgerError:
            raise
        except (OSError, UnicodeError, csv.Error) as error:
            raise Strategy2TelemetryLedgerError(
                "Could not read Strategy 2 telemetry."
            ) from error

    def _validate_loaded_record(self, record):
        try:
            if set(record) != set(self.COLUMNS) or any(
                value is None for value in record.values()
            ):
                raise ValueError("Telemetry row has the wrong column count.")
            if record["strategy_id"] != STRATEGY_ID:
                raise ValueError("Telemetry strategy ID is invalid.")
            confirmation = _parse_time(record["confirmation_time"], required=True)
            _parse_time(record["expected_entry_time"], required=True)
            for field in (
                "qualification_time", "pullback_time",
                "theoretical_entry_time", "exit_time",
            ):
                _parse_time(record[field], required=False)
            direction = _direction(record["direction"])
            token = _token_text(record["instrument_token"])
            expected_id = self.lifecycle_id(confirmation, token, direction)
            if record["record_id"] != expected_id:
                raise ValueError("Telemetry record ID is inconsistent.")
            if record["session_date"] != confirmation.astimezone(
                IST
            ).date().isoformat():
                raise ValueError("Telemetry session date is inconsistent.")
            _nonempty(record["contract_symbol"], "contract_symbol")
            for field in ("spot_at_confirmation", "atm_strike", "otm_strike"):
                _positive_text(record[field], field, required=True)
            for field in (
                "theoretical_entry_price", "target_price", "stop_price",
                "exit_price",
            ):
                _positive_text(record[field], field, required=False)
            for field in (
                "confirmation_to_subscription_latency_ms",
                "confirmation_to_entry_observation_latency_ms",
            ):
                _nonnegative_text(record[field], field)
            if record["entry_status"] not in ENTRY_STATUSES:
                raise ValueError("Telemetry entry status is invalid.")
            if record["exit_reason"] not in EXIT_REASONS:
                raise ValueError("Telemetry exit reason is invalid.")
            _deserialize_flags(record["data_quality_flags"])
        except (
            TypeError,
            ValueError,
            json.JSONDecodeError,
            Strategy2TelemetryStoreError,
        ) as error:
            raise Strategy2TelemetryLedgerError(
                "Persisted Strategy 2 telemetry row is invalid."
            ) from error

    def _ensure_root(self):
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise Strategy2TelemetryLedgerError(
                "Could not create Strategy 2 telemetry storage."
            ) from error

    def _write_ledger(self):
        self._ensure_root()
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", dir=self.root,
                prefix=f".{self.ledger_path.name}.", suffix=".tmp", delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                writer = csv.DictWriter(temporary_file, fieldnames=self.COLUMNS)
                writer.writeheader()
                writer.writerows(self._records.values())
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.ledger_path)
        except (OSError, TypeError, ValueError, csv.Error) as error:
            _discard(temporary_path)
            raise Strategy2TelemetryLedgerError(
                "Could not save Strategy 2 telemetry ledger."
            ) from error

    def _write_summary(self):
        self._ensure_root()
        updated_at = _aware_time(self._clock(), "telemetry clock")
        document = self._summary(updated_at)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.root,
                prefix=f".{self.summary_path.name}.", suffix=".tmp", delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(
                    document, temporary_file, indent=2, sort_keys=False,
                    allow_nan=False,
                )
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, self.summary_path)
        except (OSError, TypeError, ValueError) as error:
            _discard(temporary_path)
            raise Strategy2TelemetrySummaryError(
                "Could not save Strategy 2 telemetry summary cache."
            ) from error

    def _summary(self, updated_at):
        records = tuple(self._records.values())
        return {
            "strategy_id": STRATEGY_ID,
            "total_confirmations": len(records),
            "theoretical_entries": sum(
                bool(record["theoretical_entry_time"]) for record in records
            ),
            "missed_entries": sum(
                bool(record["missed_entry_reason"]) for record in records
            ),
            "closed_trades": sum(bool(record["exit_time"]) for record in records),
            "targets": sum(record["exit_reason"] == "TARGET" for record in records),
            "stops": sum(record["exit_reason"] == "STOP" for record in records),
            "open_shadow_positions": sum(
                bool(record["theoretical_entry_time"]) and not record["exit_time"]
                for record in records
            ),
            "last_updated": updated_at.astimezone(IST).isoformat(),
        }


def _aware_time(value, name):
    if not isinstance(value, datetime):
        raise Strategy2TelemetryStoreError(f"{name} must be a datetime.")
    if value.tzinfo is None or value.utcoffset() is None:
        raise Strategy2TelemetryStoreError(f"{name} must be timezone-aware.")
    return value


def _parse_time(value, required):
    if not value:
        if required:
            raise ValueError("Required timestamp is missing.")
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware.")
    return parsed


def _optional_time(value):
    if value is None:
        return ""
    return _aware_time(value, "optional timestamp").astimezone(IST).isoformat()


def _price(value, name):
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise Strategy2TelemetryStoreError(f"{name} must be finite and positive.")
    return float(value)


def _positive_text(value, name, required):
    if value == "" and not required:
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be finite and positive.")
    return number


def _nonnegative_text(value, name):
    if value == "":
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be finite and non-negative.")
    return number


def _token(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Strategy2TelemetryStoreError(
            "instrument_token must be a positive integer."
        )
    return value


def _token_text(value):
    if not value or not value.isdigit():
        raise ValueError("instrument_token must be a positive integer.")
    token = int(value)
    if token <= 0:
        raise ValueError("instrument_token must be a positive integer.")
    return token


def _direction(value):
    value = str(value).strip().upper()
    if value not in ("CE", "PE"):
        raise Strategy2TelemetryStoreError("direction must be CE or PE.")
    return value


def _nonempty(value, name):
    value = str(value).strip()
    if not value:
        raise Strategy2TelemetryStoreError(f"{name} must not be empty.")
    return value


def _normalize_flags(flags):
    if flags is None:
        return set()
    if not isinstance(flags, (list, tuple, set, frozenset)):
        raise Strategy2TelemetryStoreError(
            "data_quality_flags must be a list, tuple, or set of strings."
        )
    normalized = set()
    for flag in flags:
        if not isinstance(flag, str) or not flag.strip():
            raise Strategy2TelemetryStoreError(
                "Each data-quality flag must be a non-empty string."
            )
        normalized.add(flag.strip())
    return normalized


def _serialize_flags(flags):
    return json.dumps(
        sorted(_normalize_flags(flags)), separators=(",", ":"), ensure_ascii=True
    )


def _deserialize_flags(value):
    decoded = json.loads(value)
    if not isinstance(decoded, list):
        raise ValueError("Persisted data-quality flags must be a JSON list.")
    return _normalize_flags(decoded)


def _discard(path):
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
