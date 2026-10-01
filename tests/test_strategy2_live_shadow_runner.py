import csv
from datetime import datetime
import json
from zoneinfo import ZoneInfo

import pytest

import trading.strategy2_live_shadow_runner as runner

from trading.strategy2_forward_telemetry import (
    STRATEGY_ID,
    Strategy2ForwardTelemetryStore,
)
from trading.strategy2_live_shadow_runner import (
    RestrictedKiteClient,
    ShadowSafetyError,
    Strategy2LiveObservationLog,
    UnfinishedForwardLifecycleError,
    assert_clean_forward_lifecycle,
    build_shadow_market,
)


IST = ZoneInfo("Asia/Kolkata")


class OrderCapableKite:
    api_key = "test-api-key"
    access_token = "secret-access-token"

    def __init__(self):
        self.order_calls = 0

    def historical_data(self, *args, **kwargs):
        return []

    def instruments(self, *args, **kwargs):
        return []

    def place_order(self, *args, **kwargs):
        self.order_calls += 1
        raise AssertionError("Underlying order method must never be reached.")


def test_restricted_kite_blocks_every_order_method():
    raw = OrderCapableKite()
    kite = RestrictedKiteClient(raw.api_key, raw.access_token)

    for method in (kite.place_order, kite.modify_order, kite.cancel_order):
        with pytest.raises(ShadowSafetyError):
            method()

    assert raw.order_calls == 0


def test_restricted_kite_retains_no_original_client_bound_method_or_closure():
    raw = OrderCapableKite()
    kite = RestrictedKiteClient(raw.api_key, raw.access_token)

    assert not hasattr(kite, "_kite")
    assert raw not in vars(kite).values()
    for value in vars(kite).values():
        assert getattr(value, "__self__", None) is not raw
        closure = getattr(value, "__closure__", None) or ()
        assert all(cell.cell_contents is not raw for cell in closure)
        assert not any(
            callable(getattr(value, method, None))
            for method in ("place_order", "modify_order", "cancel_order")
        )


def test_restricted_kite_supports_required_get_only_data_operations(monkeypatch):
    class Response:
        def __init__(self, body):
            self.body = body.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self.body

    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        if "/historical/" in request.full_url:
            return Response(json.dumps({
                "status": "success",
                "data": {"candles": [[
                    "2026-09-30T09:15:00+05:30",
                    25000, 25010, 24990, 25005, 100,
                ]]},
            }))
        return Response(
            "instrument_token,exchange_token,tradingsymbol,name,last_price,"
            "expiry,strike,tick_size,lot_size,instrument_type,segment,exchange\n"
            "101,1,NIFTY 50,NIFTY,0,,0,0.05,1,EQ,NSE,NSE\n"
        )

    monkeypatch.setattr(runner, "urlopen", fake_urlopen)
    kite = RestrictedKiteClient("key", "token")

    instruments = kite.instruments()
    candles = kite.historical_data(
        101,
        datetime(2026, 9, 30, 9, 15, tzinfo=IST),
        datetime(2026, 9, 30, 9, 16, tzinfo=IST),
        "minute",
    )

    assert kite.access_token == "token"
    assert instruments[0]["instrument_token"] == 101
    assert candles[0]["close"] == 25005
    assert all(request.get_method() == "GET" for request, _ in requests)
    assert all(timeout == 7 for _, timeout in requests)


def test_shadow_market_constructs_no_strategy1_or_execution_components(tmp_path):
    store = Strategy2ForwardTelemetryStore(root=tmp_path / "forward")
    market = build_shadow_market(OrderCapableKite(), telemetry_store=store)

    assert not hasattr(market, "strategy_engine")
    assert not hasattr(market, "paper_execution_engine")
    assert not hasattr(market, "paper_trade_ledger")
    assert not hasattr(market, "execution_router")
    assert not hasattr(market, "live_execution_coordinator")


def test_observation_log_is_jsonl_and_refuses_secret_fields(tmp_path):
    log = Strategy2LiveObservationLog(root=tmp_path)
    observed = datetime(2026, 9, 30, 9, 15, tzinfo=IST)
    log.record("RUNNER_START", receipt_time=observed)

    record = json.loads(log.path.read_text(encoding="utf-8"))
    assert record["event_type"] == "RUNNER_START"
    assert record["receipt_time"] == observed.isoformat()
    assert "secret-access-token" not in log.path.read_text(encoding="utf-8")

    with pytest.raises(ShadowSafetyError):
        log.record("ERROR", access_token="must-not-appear")


@pytest.mark.parametrize("status", ["ENTRY_PENDING", "SHADOW_POSITION_ACTIVE"])
def test_unfinished_authoritative_lifecycle_fails_closed(tmp_path, status):
    store = Strategy2ForwardTelemetryStore(root=tmp_path)
    store._records["unfinished"] = {"entry_status": status}

    with pytest.raises(
        UnfinishedForwardLifecycleError,
        match="UNFINISHED_FORWARD_LIFECYCLE",
    ):
        assert_clean_forward_lifecycle(store)


def test_clean_header_only_and_completed_history_can_start(tmp_path):
    empty = Strategy2ForwardTelemetryStore(root=tmp_path / "empty")
    assert_clean_forward_lifecycle(empty)

    header_only = tmp_path / "header" / f"{STRATEGY_ID}_forward_trades.csv"
    header_only.parent.mkdir(parents=True)
    with header_only.open("w", encoding="utf-8", newline="") as target:
        csv.writer(target).writerow(Strategy2ForwardTelemetryStore.COLUMNS)
    assert_clean_forward_lifecycle(
        Strategy2ForwardTelemetryStore(root=header_only.parent)
    )

    completed = Strategy2ForwardTelemetryStore(root=tmp_path / "completed")
    completed._records["closed"] = {"entry_status": "CLOSED"}
    completed._records["missed"] = {
        "entry_status": "MISSED_EXPECTED_ENTRY_MINUTE"
    }
    assert_clean_forward_lifecycle(completed)


def test_runner_records_clean_start_and_stop_without_live_execution(
    tmp_path, monkeypatch
):
    class FakeAuth:
        def get_authenticated_client(self):
            return OrderCapableKite()

    class FakeMarket:
        def warm_indicators_from_history(self, symbol):
            assert symbol == "NIFTY 50"

        def connect_strategy2_live_shadow(self, symbol):
            assert symbol == "NIFTY 50"

        def disconnect_strategy2_live_shadow(self):
            self.disconnected = True

    fake_market = FakeMarket()
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(runner, "ZerodhaAuth", FakeAuth)
    monkeypatch.setattr(
        runner,
        "build_shadow_market",
        lambda *args, **kwargs: fake_market,
    )

    runner.run()

    path = tmp_path / "strategy2_forward" / runner.OBSERVATION_FILENAME
    event_names = [
        json.loads(line)["event_type"]
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert event_names == ["RUNNER_START", "RUNNER_STOP"]
    assert fake_market.disconnected is True
