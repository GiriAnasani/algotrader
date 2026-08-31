from datetime import datetime

import pytest

import trading.market as market_module
from core.production_application import ProductionApplication, ProductionApplicationState
from core.production_health import ProductionHealthInspector, ProductionHealthState
from core.production_preflight import ProductionPreflightInspector
from tests.test_production_application import authenticator, builder
from trading.live_readiness import LiveReadinessState
from trading.ohlc import EXCHANGE_TIMEZONE
from trading.strategy import SignalAction, StrategyResult


NOW = datetime(2026, 8, 31, 9, 15, tzinfo=EXCHANGE_TIMEZONE)


class CertificationClient:
    def __init__(self):
        self.access_token = "test-token"
        self.positions_calls = 0
        self.orders_calls = 0
        self.order_history_calls = 0
        self.place_order_calls = 0
        self.modify_order_calls = 0
        self.cancel_order_calls = 0

    def positions(self):
        self.positions_calls += 1
        return {"net": [], "day": []}

    def orders(self):
        self.orders_calls += 1
        return []

    def place_order(self, **kwargs):
        self.place_order_calls += 1
        return "certification-order-1"

    def order_history(self, order_id):
        self.order_history_calls += 1
        return [{
            "order_id": order_id,
            "status": "COMPLETE",
            "quantity": 65,
            "filled_quantity": 65,
            "pending_quantity": 0,
            "average_price": 105.0,
            "exchange_update_timestamp": "2026-08-31 09:15:00",
        }]

    def modify_order(self, **kwargs):
        self.modify_order_calls += 1

    def cancel_order(self, **kwargs):
        self.cancel_order_calls += 1


class CertificationInstruments:
    def get_nifty_index_token(self):
        return 100

    def get_nifty_option_pair(self, spot_price):
        return {
            "CE": {
                "instrument_token": 101,
                "tradingsymbol": "NIFTY26AUG25000CE",
                "lot_size": 65,
            },
            "PE": {
                "instrument_token": 102,
                "tradingsymbol": "NIFTY26AUG25000PE",
                "lot_size": 65,
            },
        }


class CertificationTicker:
    MODE_FULL = "full"
    latest = None

    def __init__(self, api_key, access_token):
        type(self).latest = self
        self.connect_calls = 0
        self.close_calls = 0
        self.subscriptions = []
        self.on_connect = self.on_ticks = self.on_close = self.on_error = None

    def connect(self):
        self.connect_calls += 1
        self.on_connect(self, {})

    def subscribe(self, tokens):
        self.subscriptions.append(tuple(tokens))

    def set_mode(self, mode, tokens):
        pass

    def close(self):
        self.close_calls += 1


def test_complete_production_application_readiness_chain(tmp_path, monkeypatch):
    client = CertificationClient()
    application = ProductionApplication(
        builder(tmp_path), authenticator(client), CertificationInstruments()
    )
    assert application.state is ProductionApplicationState.CREATED
    assert client.positions_calls == client.orders_calls == 0
    assert client.place_order_calls == client.order_history_calls == 0

    application.start(NOW)
    runtime = application.components.runtime
    market = application.components.market
    assert application.state is ProductionApplicationState.INITIALIZED
    assert client.positions_calls == client.orders_calls == 1
    assert client.place_order_calls == client.order_history_calls == 0

    readiness_before = runtime.readiness_gate.state
    enablement_before = runtime.execution_coordinator.enabled
    assert ProductionPreflightInspector().inspect(application).passed
    assert runtime.readiness_gate.state is readiness_before
    assert runtime.execution_coordinator.enabled is enablement_before
    assert client.positions_calls == client.orders_calls == 1

    risk_calls = []
    attempt_calls = []
    original_evaluate = runtime.risk_evaluator.evaluate
    original_record_attempt = runtime.execution_guard.record_attempt

    def evaluate(*args, **kwargs):
        risk_calls.append(args)
        return original_evaluate(*args, **kwargs)

    def record_attempt(*args, **kwargs):
        attempt_calls.append(args)
        return original_record_attempt(*args, **kwargs)

    monkeypatch.setattr(runtime.risk_evaluator, "evaluate", evaluate)
    monkeypatch.setattr(runtime.execution_guard, "record_attempt", record_attempt)
    monkeypatch.setattr(market_module, "KiteTicker", CertificationTicker)
    market._live_market_data_clock = lambda: NOW
    application.run()
    ticker = CertificationTicker.latest
    assert ticker.connect_calls == 1
    assert application.state is ProductionApplicationState.RUNNING

    ticker.on_ticks(ticker, [{
        "instrument_token": 100,
        "last_price": 25000.0,
        "exchange_timestamp": NOW,
    }])
    ticker.on_ticks(ticker, [{
        "instrument_token": 101,
        "last_price": 100.0,
        "exchange_timestamp": NOW,
    }])
    result = StrategyResult(
        "certification", SignalAction.BUY_CE, NOW,
        actions=(SignalAction.BUY_CE,),
    )
    market._execute_strategy_result(result, NOW)

    opened = runtime.position_manager.active_position
    assert len(risk_calls) == len(attempt_calls) == 1
    assert client.positions_calls == 2
    assert client.orders_calls == 1
    assert client.place_order_calls == 1
    assert client.order_history_calls == 1
    assert client.modify_order_calls == client.cancel_order_calls == 0
    assert opened is not None
    assert market.live_execution_context.contract_symbol == opened.contract_symbol
    assert application.components.position_store.load().contract_symbol == (
        opened.contract_symbol
    )
    assert application.components.config.log_directory.joinpath(
        "audit.jsonl"
    ).exists()
    assert ProductionHealthInspector(runtime, market).inspect(NOW).state is (
        ProductionHealthState.HEALTHY
    )
    assert application.components.runtime_pnl.session_net_pnl_aggregator is (
        runtime.risk_evaluator.session_net_pnl_aggregator
    )
    report = application.components.runtime_pnl.report(NOW.date(), 105.0)
    assert report.open_position_count == 1

    assert application.shutdown() is True
    assert application.state is ProductionApplicationState.STOPPED
    assert runtime.execution_coordinator.enabled is False
    assert runtime.readiness_gate.state is LiveReadinessState.NOT_READY
    assert ticker.close_calls == 1
    assert runtime.position_manager.active_position is opened
    assert client.place_order_calls == 1
    assert application.shutdown() is False
    assert ticker.close_calls == 1
