import csv
from datetime import datetime, timedelta
import json
from zoneinfo import ZoneInfo

import pytest

from trading.candle import Candle
from trading.market import MarketData
from trading.strategy import IndicatorSnapshot, SignalAction, StrategyResult
from trading.strategy2_forward_telemetry import (
    NEGATIVE_ENTRY_LATENCY,
    NEGATIVE_SUBSCRIPTION_LATENCY,
    STRATEGY_ID,
    Strategy2ForwardTelemetryStore,
    Strategy2TelemetryLedgerError,
    Strategy2TelemetrySummaryError,
    Strategy2TelemetryStoreError,
)
from trading.strategy2_pending_entry import Strategy2PendingEntryManager


IST = ZoneInfo("Asia/Kolkata")
CONFIRMATION = datetime(2026, 9, 29, 9, 52, tzinfo=IST)
ENTRY = datetime(2026, 9, 29, 9, 53, 1, tzinfo=IST)
EXIT = datetime(2026, 9, 29, 9, 54, tzinfo=IST)


def contract():
    return {
        "instrument_token": 202,
        "tradingsymbol": "TESTCE",
        "expiry": CONFIRMATION.date(),
        "atm_strike": 25000.0,
        "otm_strike": 25050.0,
        "strike": 25050.0,
        "lot_size": 65,
        "option_type": "CE",
    }


def confirmation(store):
    record_id = store.record_confirmation(
        direction="CE",
        qualification_time=datetime(2026, 9, 29, 9, 49, tzinfo=IST),
        pullback_time=datetime(2026, 9, 29, 9, 51, tzinfo=IST),
        confirmation_time=CONFIRMATION,
        spot_at_confirmation=25024.0,
        contract=contract(),
        expected_entry_time=datetime(2026, 9, 29, 9, 53, tzinfo=IST),
    )
    store.record_subscription(
        record_id, CONFIRMATION + timedelta(milliseconds=250)
    )
    return record_id


def read_rows(store):
    with store.ledger_path.open("r", encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def read_summary(store):
    return json.loads(store.summary_path.read_text(encoding="utf-8"))


def test_confirmation_persists_one_deterministic_lifecycle(tmp_path):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: CONFIRMATION)

    first_id = confirmation(store)
    second_id = confirmation(store)
    rows = read_rows(store)

    assert first_id == second_id
    assert first_id == f"{STRATEGY_ID}|{CONFIRMATION.isoformat()}|202|CE"
    assert len(rows) == 1
    assert tuple(rows[0]) == store.COLUMNS
    assert rows[0]["strategy_id"] == STRATEGY_ID
    assert rows[0]["session_date"] == "2026-09-29"
    assert rows[0]["direction"] == "CE"
    assert rows[0]["qualification_time"].endswith("+05:30")
    assert rows[0]["pullback_time"].endswith("+05:30")
    assert rows[0]["confirmation_time"].endswith("+05:30")
    assert rows[0]["atm_strike"] == "25000.0"
    assert rows[0]["otm_strike"] == "25050.0"
    assert rows[0]["contract_symbol"] == "TESTCE"
    assert rows[0]["instrument_token"] == "202"
    assert rows[0]["expected_entry_time"].endswith("+05:30")
    assert rows[0]["entry_status"] == "ENTRY_PENDING"
    assert rows[0]["confirmation_to_subscription_latency_ms"] == "250.0"
    assert rows[0]["data_quality_flags"] == "[]"


def test_default_location_respects_jarvis_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))

    store = Strategy2ForwardTelemetryStore(clock=lambda: CONFIRMATION)

    assert store.root == tmp_path / "strategy2_forward"
    assert store.ledger_path.name == f"{STRATEGY_ID}_forward_trades.csv"
    assert store.summary_path.name == f"{STRATEGY_ID}_forward_summary.json"


def test_entry_updates_same_row_with_fixed_levels_and_summary(tmp_path):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: ENTRY)
    record_id = confirmation(store)

    store.record_theoretical_entry(
        record_id,
        entry_time=ENTRY,
        entry_price=100.0,
        target_price=135.0,
        stop_price=85.0,
    )
    store.record_theoretical_entry(
        record_id,
        entry_time=ENTRY,
        entry_price=100.0,
        target_price=135.0,
        stop_price=85.0,
    )

    rows = read_rows(store)
    assert len(rows) == 1
    assert rows[0]["theoretical_entry_time"].endswith("+05:30")
    assert rows[0]["theoretical_entry_price"] == "100.0"
    assert rows[0]["target_price"] == "135.0"
    assert rows[0]["stop_price"] == "85.0"
    assert rows[0]["entry_status"] == "SHADOW_POSITION_ACTIVE"
    assert rows[0]["confirmation_to_entry_observation_latency_ms"] == "61000.0"
    assert read_summary(store) == {
        "strategy_id": STRATEGY_ID,
        "total_confirmations": 1,
        "theoretical_entries": 1,
        "missed_entries": 0,
        "closed_trades": 0,
        "targets": 0,
        "stops": 0,
        "open_shadow_positions": 1,
        "last_updated": ENTRY.isoformat(),
    }


@pytest.mark.parametrize("reason", ["TARGET", "STOP"])
def test_exit_updates_same_lifecycle_once_and_survives_restart(tmp_path, reason):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: EXIT)
    record_id = confirmation(store)
    store.record_theoretical_entry(
        record_id,
        entry_time=ENTRY,
        entry_price=100.0,
        target_price=135.0,
        stop_price=85.0,
    )
    price = 135.0 if reason == "TARGET" else 85.0

    store.record_exit(
        record_id, exit_time=EXIT, exit_price=price, exit_reason=reason
    )
    store.record_exit(
        record_id, exit_time=EXIT, exit_price=price, exit_reason=reason
    )

    restarted = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: EXIT)
    rows = read_rows(restarted)
    summary = read_summary(restarted)
    assert len(rows) == 1
    assert rows[0]["exit_time"].endswith("+05:30")
    assert rows[0]["exit_price"] == str(price)
    assert rows[0]["exit_reason"] == reason
    assert rows[0]["entry_status"] == "CLOSED"
    assert summary["closed_trades"] == 1
    assert summary["targets"] == (1 if reason == "TARGET" else 0)
    assert summary["stops"] == (1 if reason == "STOP" else 0)
    assert summary["open_shadow_positions"] == 0


def test_missed_entry_is_auditable_without_active_trade(tmp_path):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: EXIT)
    record_id = store.record_confirmation(
        direction="CE",
        qualification_time=None,
        pullback_time=None,
        confirmation_time=CONFIRMATION,
        spot_at_confirmation=25024.0,
        contract=contract(),
        expected_entry_time=CONFIRMATION + timedelta(minutes=1),
    )

    store.record_missed_entry(record_id, "MISSED_EXPECTED_ENTRY_MINUTE")
    store.record_missed_entry(record_id, "MISSED_EXPECTED_ENTRY_MINUTE")

    row = read_rows(store)[0]
    assert row["qualification_time"] == ""
    assert row["pullback_time"] == ""
    assert row["theoretical_entry_time"] == ""
    assert row["theoretical_entry_price"] == ""
    assert row["entry_status"] == "MISSED_EXPECTED_ENTRY_MINUTE"
    assert row["missed_entry_reason"] == "MISSED_EXPECTED_ENTRY_MINUTE"
    assert read_summary(store)["missed_entries"] == 1
    assert read_summary(store)["open_shadow_positions"] == 0


def test_non_finite_prices_are_rejected(tmp_path):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: ENTRY)
    record_id = confirmation(store)

    with pytest.raises(Strategy2TelemetryStoreError):
        store.record_theoretical_entry(
            record_id,
            entry_time=ENTRY,
            entry_price=float("nan"),
            target_price=135.0,
            stop_price=85.0,
        )


class FakeTicker:
    MODE_FULL = "full"

    def subscribe(self, tokens):
        self.tokens = tokens

    def set_mode(self, mode, tokens):
        self.mode = (mode, tokens)


class FakeEngine:
    def __init__(self):
        self.active_position = None
        self.last_signal_qualification_time = datetime(
            2026, 9, 29, 9, 49, tzinfo=IST
        )
        self.last_signal_pullback_time = datetime(
            2026, 9, 29, 9, 51, tzinfo=IST
        )

    def evaluate(self, snapshot, option_premiums=None):
        return StrategyResult(
            strategy_name=STRATEGY_ID,
            action=SignalAction.BUY_CE,
            candle_time=snapshot.candle.time,
            reason="test",
        )

    def set_position_active(self, side):
        self.active_position = side

    def set_position_closed(self):
        self.active_position = None


class FakeInstruments:
    def get_nifty_strategy2_contract(self, spot_price, option_type, as_of=None):
        return contract()


def telemetry_market(store):
    market = MarketData.__new__(MarketData)
    market.strategy2_engine = FakeEngine()
    market.strategy2_pending_entry_manager = Strategy2PendingEntryManager()
    market.latest_strategy2_result = None
    market.latest_option_premiums = {"CE": 99.0}
    market.instruments = FakeInstruments()
    market._live_ticker = FakeTicker()
    market._live_market_data_clock = lambda: CONFIRMATION + timedelta(
        milliseconds=500
    )
    market.strategy2_option_tokens = {}
    market.strategy2_pending_contract = None
    market.strategy2_theoretical_entry = None
    market.strategy2_theoretical_entry_error = None
    market.strategy2_shadow_position = None
    market.strategy2_shadow_exit = None
    market.strategy2_telemetry_store = store
    market.strategy2_telemetry_record_id = None
    return market


def test_market_lifecycle_updates_one_telemetry_record_without_strategy1_state(
    tmp_path,
):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: EXIT)
    market = telemetry_market(store)
    market.option_tokens = {999: "PE"}
    snapshot = IndicatorSnapshot(
        candle=Candle(
            CONFIRMATION, 25010.0, 25030.0, 25000.0, 25024.0
        ),
        values={"ema": {9: 25020.0, 20: 25010.0}},
    )

    market._process_strategy2_shadow(snapshot)
    market._handle_strategy2_option_tick({
        "instrument_token": 202,
        "last_price": 100.0,
        "exchange_timestamp": ENTRY,
    })
    market._handle_strategy2_option_tick({
        "instrument_token": 202,
        "last_price": 135.0,
        "exchange_timestamp": EXIT,
    })

    row = read_rows(store)[0]
    assert len(read_rows(store)) == 1
    assert row["spot_at_confirmation"] == "25024.0"
    assert row["target_price"] == "135.0"
    assert row["stop_price"] == "85.0"
    assert row["exit_reason"] == "TARGET"
    assert market.option_tokens == {999: "PE"}


def test_atomic_write_failure_is_not_silenced(tmp_path, monkeypatch):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: CONFIRMATION)

    def fail_replace(source, destination):
        raise OSError("disk unavailable")

    monkeypatch.setattr("trading.strategy2_forward_telemetry.os.replace", fail_replace)

    with pytest.raises(Strategy2TelemetryStoreError):
        confirmation(store)

    assert store.records == ()


def test_summary_failure_keeps_csv_commit_and_restart_repairs_cache(
    tmp_path, monkeypatch
):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: ENTRY)
    record_id = confirmation(store)

    def fail_summary():
        raise Strategy2TelemetrySummaryError("summary unavailable")

    monkeypatch.setattr(store, "_write_summary", fail_summary)
    with pytest.raises(Strategy2TelemetrySummaryError):
        store.record_theoretical_entry(
            record_id,
            entry_time=ENTRY,
            entry_price=100.0,
            target_price=135.0,
            stop_price=85.0,
        )

    assert read_rows(store)[0]["entry_status"] == "SHADOW_POSITION_ACTIVE"
    assert read_summary(store)["theoretical_entries"] == 0

    restarted = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: ENTRY)
    assert read_summary(restarted)["theoretical_entries"] == 1
    assert read_summary(restarted)["open_shadow_positions"] == 1


def test_restart_recreates_missing_and_replaces_stale_summary(tmp_path):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: ENTRY)
    record_id = confirmation(store)
    store.record_theoretical_entry(
        record_id,
        entry_time=ENTRY,
        entry_price=100.0,
        target_price=135.0,
        stop_price=85.0,
    )

    store.summary_path.unlink()
    restarted = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: ENTRY)
    assert read_summary(restarted)["theoretical_entries"] == 1

    restarted.summary_path.write_text(
        json.dumps({"strategy_id": "STALE", "total_confirmations": 999}),
        encoding="utf-8",
    )
    repaired = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: ENTRY)
    assert read_summary(repaired)["strategy_id"] == STRATEGY_ID
    assert read_summary(repaired)["total_confirmations"] == 1


def test_header_only_csv_reconstructs_zero_summary(tmp_path):
    ledger = tmp_path / f"{STRATEGY_ID}_forward_trades.csv"
    tmp_path.mkdir(parents=True, exist_ok=True)
    with ledger.open("w", encoding="utf-8", newline="") as target:
        csv.DictWriter(
            target, fieldnames=Strategy2ForwardTelemetryStore.COLUMNS
        ).writeheader()

    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: CONFIRMATION)

    assert store.records == ()
    assert read_summary(store)["total_confirmations"] == 0
    assert read_summary(store)["open_shadow_positions"] == 0


def write_rows(store, rows):
    with store.ledger_path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=store.COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("record_id", "invalid-record-id"),
        ("confirmation_time", "not-a-timestamp"),
        ("theoretical_entry_price", "nan"),
        ("exit_price", "inf"),
        ("entry_status", "UNKNOWN"),
    ],
)
def test_corrupt_csv_values_are_rejected(tmp_path, field, value):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: ENTRY)
    confirmation(store)
    row = read_rows(store)[0]
    row[field] = value
    write_rows(store, [row])

    with pytest.raises(Strategy2TelemetryLedgerError):
        Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: ENTRY)


def test_truncated_csv_row_is_rejected(tmp_path):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: ENTRY)
    confirmation(store)
    lines = store.ledger_path.read_text(encoding="utf-8").splitlines()
    store.ledger_path.write_text(
        lines[0] + "\n" + ",".join(lines[1].split(",")[:-1]) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(Strategy2TelemetryLedgerError):
        Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: ENTRY)


def test_duplicate_persisted_ids_are_rejected(tmp_path):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: ENTRY)
    confirmation(store)
    row = read_rows(store)[0]
    write_rows(store, [row, row])

    with pytest.raises(Strategy2TelemetryLedgerError):
        Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: ENTRY)


def test_negative_latencies_are_empty_and_flagged(tmp_path):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: ENTRY)
    record_id = store.record_confirmation(
        direction="CE",
        qualification_time=None,
        pullback_time=None,
        confirmation_time=CONFIRMATION,
        spot_at_confirmation=25024.0,
        contract=contract(),
        expected_entry_time=CONFIRMATION + timedelta(minutes=1),
    )
    store.record_subscription(record_id, CONFIRMATION - timedelta(seconds=1))
    store.record_theoretical_entry(
        record_id,
        entry_time=CONFIRMATION - timedelta(seconds=2),
        entry_price=100.0,
        target_price=135.0,
        stop_price=85.0,
    )

    row = read_rows(store)[0]
    assert row["confirmation_to_subscription_latency_ms"] == ""
    assert row["confirmation_to_entry_observation_latency_ms"] == ""
    assert json.loads(row["data_quality_flags"]) == sorted([
        NEGATIVE_SUBSCRIPTION_LATENCY,
        NEGATIVE_ENTRY_LATENCY,
    ])


def test_data_quality_flags_are_sorted_unique_and_reject_objects(tmp_path):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: CONFIRMATION)
    record_id = store.record_confirmation(
        direction="CE",
        qualification_time=None,
        pullback_time=None,
        confirmation_time=CONFIRMATION,
        spot_at_confirmation=25024.0,
        contract=contract(),
        expected_entry_time=CONFIRMATION + timedelta(minutes=1),
        data_quality_flags={"Z_FLAG", "A_FLAG", "Z_FLAG"},
    )
    assert json.loads(read_rows(store)[0]["data_quality_flags"]) == [
        "A_FLAG", "Z_FLAG"
    ]

    with pytest.raises(Strategy2TelemetryStoreError):
        store.record_confirmation(
            direction="PE",
            qualification_time=None,
            pullback_time=None,
            confirmation_time=CONFIRMATION + timedelta(minutes=1),
            spot_at_confirmation=25024.0,
            contract={**contract(), "instrument_token": 203, "option_type": "PE"},
            expected_entry_time=CONFIRMATION + timedelta(minutes=2),
            data_quality_flags=object(),
        )
    assert record_id in {record["record_id"] for record in store.records}


def fail_telemetry(*args, **kwargs):
    raise Strategy2TelemetryLedgerError("telemetry unavailable")


def test_confirmation_failure_leaves_no_pending_runtime_transition(
    tmp_path, monkeypatch
):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: CONFIRMATION)
    market = telemetry_market(store)
    monkeypatch.setattr(store, "record_confirmation", fail_telemetry)
    snapshot = IndicatorSnapshot(
        candle=Candle(CONFIRMATION, 25010.0, 25030.0, 25000.0, 25024.0),
        values={"ema": {9: 25020.0, 20: 25010.0}},
    )

    with pytest.raises(Strategy2TelemetryLedgerError):
        market._process_strategy2_shadow(snapshot)

    assert market.strategy2_pending_entry_manager.pending_entry is None
    assert market.strategy2_pending_contract is None
    assert market.strategy2_option_tokens == {}


def test_entry_failure_leaves_pending_and_does_not_activate(
    tmp_path, monkeypatch
):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: ENTRY)
    market = telemetry_market(store)
    snapshot = IndicatorSnapshot(
        candle=Candle(CONFIRMATION, 25010.0, 25030.0, 25000.0, 25024.0),
        values={"ema": {9: 25020.0, 20: 25010.0}},
    )
    market._process_strategy2_shadow(snapshot)
    monkeypatch.setattr(store, "record_theoretical_entry", fail_telemetry)

    with pytest.raises(Strategy2TelemetryLedgerError):
        market._handle_strategy2_option_tick({
            "instrument_token": 202,
            "last_price": 100.0,
            "exchange_timestamp": ENTRY,
        })

    assert market.strategy2_pending_entry_manager.pending_entry is not None
    assert market.strategy2_shadow_position is None
    assert market.strategy2_engine.active_position is None


def test_miss_failure_keeps_pending_runtime_state(tmp_path, monkeypatch):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: EXIT)
    market = telemetry_market(store)
    snapshot = IndicatorSnapshot(
        candle=Candle(CONFIRMATION, 25010.0, 25030.0, 25000.0, 25024.0),
        values={"ema": {9: 25020.0, 20: 25010.0}},
    )
    market._process_strategy2_shadow(snapshot)
    monkeypatch.setattr(store, "record_missed_entry", fail_telemetry)

    with pytest.raises(Strategy2TelemetryLedgerError):
        market._handle_strategy2_option_tick({
            "instrument_token": 202,
            "last_price": 100.0,
            "exchange_timestamp": EXIT,
        })

    assert market.strategy2_pending_entry_manager.pending_entry is not None
    assert market.strategy2_pending_contract is not None
    assert market.strategy2_option_tokens == {202: "CE"}


def test_exit_failure_keeps_position_active_and_engine_open(
    tmp_path, monkeypatch
):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: EXIT)
    market = telemetry_market(store)
    snapshot = IndicatorSnapshot(
        candle=Candle(CONFIRMATION, 25010.0, 25030.0, 25000.0, 25024.0),
        values={"ema": {9: 25020.0, 20: 25010.0}},
    )
    market._process_strategy2_shadow(snapshot)
    market._handle_strategy2_option_tick({
        "instrument_token": 202,
        "last_price": 100.0,
        "exchange_timestamp": ENTRY,
    })
    monkeypatch.setattr(store, "record_exit", fail_telemetry)

    with pytest.raises(Strategy2TelemetryLedgerError):
        market._handle_strategy2_option_tick({
            "instrument_token": 202,
            "last_price": 135.0,
            "exchange_timestamp": EXIT,
        })

    assert market.strategy2_shadow_position["active"] is True
    assert market.strategy2_shadow_exit is None
    assert market.strategy2_engine.active_position == "CE"
    assert market.strategy2_option_tokens == {202: "CE"}


def fail_summary_once(store, monkeypatch):
    original = store._write_summary
    calls = 0

    def wrapped():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise Strategy2TelemetrySummaryError("summary unavailable")
        return original()

    monkeypatch.setattr(store, "_write_summary", wrapped)


def prepared_market(store):
    market = telemetry_market(store)
    snapshot = IndicatorSnapshot(
        candle=Candle(CONFIRMATION, 25010.0, 25030.0, 25000.0, 25024.0),
        values={"ema": {9: 25020.0, 20: 25010.0}},
    )
    market._process_strategy2_shadow(snapshot)
    return market


def test_entry_summary_retry_uses_first_authoritative_tick(tmp_path, monkeypatch):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: ENTRY)
    market = prepared_market(store)
    fail_summary_once(store, monkeypatch)

    with pytest.raises(Strategy2TelemetrySummaryError):
        market._handle_strategy2_option_tick({
            "instrument_token": 202,
            "last_price": 101.5,
            "exchange_timestamp": ENTRY,
        })

    assert market.strategy2_pending_entry_manager.pending_entry is not None
    assert market.strategy2_shadow_position is None
    later = ENTRY + timedelta(seconds=19)
    assert market._handle_strategy2_option_tick({
        "instrument_token": 202,
        "last_price": 105.0,
        "exchange_timestamp": later,
    }) is True

    position = market.strategy2_shadow_position
    assert position["entry_time"] == ENTRY
    assert position["entry_price"] == 101.5
    assert position["target_price"] == 101.5 * 1.35
    assert position["stop_price"] == 101.5 * 0.85
    assert len(read_rows(store)) == 1
    assert read_rows(store)[0]["theoretical_entry_price"] == "101.5"


def test_late_tick_reconciles_persisted_entry_instead_of_missing(
    tmp_path, monkeypatch
):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: ENTRY)
    market = prepared_market(store)
    fail_summary_once(store, monkeypatch)

    with pytest.raises(Strategy2TelemetrySummaryError):
        market._handle_strategy2_option_tick({
            "instrument_token": 202,
            "last_price": 101.5,
            "exchange_timestamp": ENTRY,
        })

    assert market._handle_strategy2_option_tick({
        "instrument_token": 202,
        "last_price": 110.0,
        "exchange_timestamp": EXIT,
    }) is True
    assert market.strategy2_shadow_position["active"] is True
    assert market.strategy2_shadow_position["entry_time"] == ENTRY
    assert market.strategy2_shadow_position["entry_price"] == 101.5
    assert market.strategy2_theoretical_entry_error is None
    row = read_rows(store)[0]
    assert row["entry_status"] == "SHADOW_POSITION_ACTIVE"
    assert row["missed_entry_reason"] == ""


@pytest.mark.parametrize(
    ("first_price", "expected_reason", "retry_price"),
    [
        (135.0, "TARGET", 80.0),
        (85.0, "STOP", 140.0),
        (135.0, "TARGET", 100.0),
    ],
)
def test_exit_summary_retry_uses_authoritative_terminal_event(
    tmp_path, monkeypatch, first_price, expected_reason, retry_price
):
    store = Strategy2ForwardTelemetryStore(tmp_path, clock=lambda: EXIT)
    market = prepared_market(store)
    market._handle_strategy2_option_tick({
        "instrument_token": 202,
        "last_price": 100.0,
        "exchange_timestamp": ENTRY,
    })
    fail_summary_once(store, monkeypatch)

    with pytest.raises(Strategy2TelemetrySummaryError):
        market._handle_strategy2_option_tick({
            "instrument_token": 202,
            "last_price": first_price,
            "exchange_timestamp": EXIT,
        })

    assert market.strategy2_shadow_position["active"] is True
    retry_time = EXIT + timedelta(seconds=20)
    assert market._handle_strategy2_option_tick({
        "instrument_token": 202,
        "last_price": retry_price,
        "exchange_timestamp": retry_time,
    }) is True

    assert market.strategy2_shadow_position["active"] is False
    assert market.strategy2_shadow_exit == {
        "exit_time": EXIT,
        "exit_price": first_price,
        "exit_reason": expected_reason,
    }
    assert market.strategy2_engine.active_position is None
    row = read_rows(store)[0]
    assert row["exit_time"] == EXIT.isoformat()
    assert row["exit_price"] == str(first_price)
    assert row["exit_reason"] == expected_reason
    assert len(read_rows(store)) == 1
