from datetime import datetime
from types import SimpleNamespace

import pytest

from core.production_application import (
    ProductionApplication,
    ProductionApplicationError,
    ProductionApplicationState,
)
from core.production_auth import (
    ProductionAuthenticationResult,
    ProductionAuthenticationState,
    ProductionAuthenticator,
)
from core.production_config import DeploymentEnvironment, ProductionConfig
from core.production_startup import ProductionStartupBuilder
from trading.execution_mode import ExecutionMode
from trading.live_readiness import LiveReadinessState
from trading.market import MarketData
from trading.market_data_health import MarketDataHealthState, MarketDataHealthTracker
import trading.market as market_module
from trading.live_restart_orchestration import LiveRestartOrchestrationState
from trading.live_risk import LiveRiskLimits
from trading.ohlc import EXCHANGE_TIMEZONE
from trading.option_charges import (
    NetPnLCalculator,
    OptionChargeSchedule,
    OptionTradeChargesCalculator,
)
from trading.realized_net_pnl import RealizedNetPnLAggregator
from trading.session_net_pnl import SessionNetPnLAggregator


NOW = datetime(2026, 8, 31, 9, 15, tzinfo=EXCHANGE_TIMEZONE)


class FakeKiteClient:
    def __init__(self, orders=None):
        self.access_token = "token"
        self._orders = [] if orders is None else orders
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
        return self._orders

    def order_history(self, order_id):
        self.order_history_calls += 1
        return []

    def place_order(self, **kwargs):
        self.place_order_calls += 1
        return "order-1"

    def modify_order(self, **kwargs):
        self.modify_order_calls += 1

    def cancel_order(self, **kwargs):
        self.cancel_order_calls += 1


def config(tmp_path, mode=ExecutionMode.LIVE, enabled=True):
    return ProductionConfig(
        DeploymentEnvironment.LOCAL,
        mode,
        enabled,
        tmp_path / "state" / "position.json",
        tmp_path / "history" / "closed.json",
        tmp_path / "logs",
        2.0,
    )


def risk_pnl():
    charges = OptionTradeChargesCalculator(OptionChargeSchedule(0, 0, 0, 0, 0, 0))
    return SessionNetPnLAggregator(
        RealizedNetPnLAggregator(NetPnLCalculator(charges))
    )


def builder(tmp_path, mode=ExecutionMode.LIVE, enabled=True):
    return ProductionStartupBuilder(
        config(tmp_path, mode, enabled),
        LiveRiskLimits(1, 65, 10, 1000),
        risk_pnl(),
    )


def authenticator(client, state=ProductionAuthenticationState.AUTHENTICATED):
    value = object.__new__(ProductionAuthenticator)
    value.restore = lambda now=None: ProductionAuthenticationResult(state, client)
    return value


def live_application(tmp_path, enabled=True, client=None):
    client = client or FakeKiteClient()
    application = ProductionApplication(
        builder(tmp_path, enabled=enabled), authenticator(client), object()
    )
    return application, client


def assert_no_order_activity(client):
    assert client.order_history_calls == 0
    assert client.place_order_calls == 0
    assert client.modify_order_calls == 0
    assert client.cancel_order_calls == 0


def test_states_are_unambiguous_and_construction_is_inert(tmp_path):
    assert [state.value for state in ProductionApplicationState] == [
        "CREATED", "STARTING", "LOGIN_REQUIRED", "INITIALIZED", "RUNNING",
        "STOPPING", "STOPPED", "FAILED",
    ]
    application, client = live_application(tmp_path)
    assert application.state is ProductionApplicationState.CREATED
    assert application.components is None
    assert client.positions_calls == client.orders_calls == 0
    assert_no_order_activity(client)
    assert list(tmp_path.iterdir()) == []


def test_live_start_uses_real_recovery_and_restart_chain(tmp_path):
    application, client = live_application(tmp_path)
    result = application.start(NOW)
    runtime = application.components.runtime
    assert application.state is ProductionApplicationState.INITIALIZED
    assert result is application.restart_result
    assert result.state is LiveRestartOrchestrationState.READY_FLAT
    assert application.startup_result.runtime is runtime
    assert application.startup_result.market is application.components.market
    assert runtime.readiness_gate.state is LiveReadinessState.READY
    assert client.positions_calls == client.orders_calls == 1
    assert_no_order_activity(client)
    assert not application.components.position_store.path.exists()
    assert not application.components.closed_position_history_store.path.exists()


def test_login_required_stops_before_composition_or_broker_recovery(tmp_path):
    client = FakeKiteClient()
    application = ProductionApplication(
        builder(tmp_path),
        authenticator(client, ProductionAuthenticationState.LOGIN_REQUIRED),
        object(),
    )
    result = application.start(NOW)
    assert result.state is ProductionAuthenticationState.LOGIN_REQUIRED
    assert application.state is ProductionApplicationState.LOGIN_REQUIRED
    assert application.components is None
    assert client.positions_calls == client.orders_calls == 0
    assert_no_order_activity(client)


def test_run_requires_existing_authorization_and_uses_exact_market(tmp_path, monkeypatch):
    application, client = live_application(tmp_path)
    application.start(NOW)
    calls = []
    monkeypatch.setattr(
        application.components.market,
        "connect_live",
        lambda symbol: calls.append(symbol),
    )
    returned = application.run("NIFTY 50")
    assert returned is application.components
    assert application.state is ProductionApplicationState.RUNNING
    assert calls == ["NIFTY 50"]
    assert client.positions_calls == client.orders_calls == 1
    assert_no_order_activity(client)


def test_disabled_execution_cannot_run_but_remains_initialized(tmp_path, monkeypatch):
    application, client = live_application(tmp_path, enabled=False)
    application.start(NOW)
    monkeypatch.setattr(
        application.components.market,
        "connect_live",
        lambda symbol: pytest.fail("ticker must not connect"),
    )
    with pytest.raises(ProductionApplicationError):
        application.run()
    assert application.state is ProductionApplicationState.INITIALIZED
    assert client.positions_calls == client.orders_calls == 1
    assert_no_order_activity(client)


def test_shutdown_disables_revokes_then_disconnects_without_persisting(tmp_path, monkeypatch):
    application, client = live_application(tmp_path)
    application.start(NOW)
    runtime = application.components.runtime
    events = []
    original_disable = runtime.execution_coordinator.disable
    original_revoke = runtime.readiness_gate.revoke

    def disable():
        events.append("disable")
        original_disable()

    def revoke():
        events.append("revoke")
        original_revoke()

    monkeypatch.setattr(runtime.execution_coordinator, "disable", disable)
    monkeypatch.setattr(runtime.readiness_gate, "revoke", revoke)
    monkeypatch.setattr(
        application.components.market,
        "disconnect_live",
        lambda: events.append("disconnect") or True,
    )
    assert application.shutdown() is True
    assert events == ["disable", "revoke", "disconnect"]
    assert application.state is ProductionApplicationState.STOPPED
    assert runtime.execution_coordinator.enabled is False
    assert runtime.readiness_gate.state is LiveReadinessState.NOT_READY
    assert application.shutdown() is False
    assert not application.components.position_store.path.exists()
    assert not application.components.closed_position_history_store.path.exists()
    assert_no_order_activity(client)


def test_connect_failure_performs_partial_startup_cleanup(tmp_path, monkeypatch):
    application, client = live_application(tmp_path)
    application.start(NOW)
    runtime = application.components.runtime
    disconnected = []

    def fail_connect(symbol):
        raise RuntimeError("connect failed")

    monkeypatch.setattr(application.components.market, "connect_live", fail_connect)
    monkeypatch.setattr(
        application.components.market,
        "disconnect_live",
        lambda: disconnected.append(True) or True,
    )
    with pytest.raises(RuntimeError, match="connect failed"):
        application.run()
    assert application.state is ProductionApplicationState.FAILED
    assert runtime.execution_coordinator.enabled is False
    assert runtime.readiness_gate.state is LiveReadinessState.NOT_READY
    assert disconnected == [True]
    assert_no_order_activity(client)


def test_failure_after_ticker_acquisition_closes_exact_resource_once(
    tmp_path, monkeypatch
):
    class Instruments:
        def get_nifty_index_token(self):
            return 100

    class FailingTicker:
        MODE_FULL = "full"
        latest = None

        def __init__(self, api_key, access_token):
            type(self).latest = self
            self.close_calls = 0
            self.on_connect = self.on_ticks = self.on_close = self.on_error = None

        def connect(self):
            self.on_connect(self, {})
            raise RuntimeError("failure after ticker acquisition")

        def subscribe(self, tokens):
            pass

        def set_mode(self, mode, tokens):
            pass

        def close(self):
            self.close_calls += 1

    client = FakeKiteClient()
    application = ProductionApplication(
        builder(tmp_path), authenticator(client), Instruments()
    )
    application.start(NOW)
    runtime = application.components.runtime
    market = application.components.market
    monkeypatch.setattr(market_module, "KiteTicker", FailingTicker)

    with pytest.raises(RuntimeError, match="failure after ticker acquisition"):
        application.run()

    ticker = FailingTicker.latest
    assert application.state is ProductionApplicationState.FAILED
    assert runtime.execution_coordinator.enabled is False
    assert runtime.readiness_gate.state is LiveReadinessState.NOT_READY
    assert ticker.close_calls == 1
    assert market._live_ticker is None
    assert client.place_order_calls == 0
    assert application.shutdown() is True
    assert application.state is ProductionApplicationState.STOPPED
    assert ticker.close_calls == 1
    assert application.shutdown() is False
    assert ticker.close_calls == 1
    assert_no_order_activity(client)


def test_paper_lifecycle_needs_no_live_authorities(tmp_path):
    application = ProductionApplication(
        builder(tmp_path, ExecutionMode.PAPER, enabled=False)
    )
    components = application.start()
    assert application.state is ProductionApplicationState.INITIALIZED
    assert components.runtime is None
    assert application.run() is components
    assert application.state is ProductionApplicationState.RUNNING
    assert application.shutdown() is True
    assert application.state is ProductionApplicationState.STOPPED


def test_market_owns_and_idempotently_disconnects_exact_live_ticker(monkeypatch):
    class Instruments:
        def get_nifty_index_token(self):
            return 100

    class Ticker:
        MODE_FULL = "full"
        latest = None

        def __init__(self, api_key, access_token):
            type(self).latest = self
            self.close_calls = 0
            self.on_connect = self.on_ticks = self.on_close = self.on_error = None

        def connect(self):
            self.on_connect(self, {})

        def subscribe(self, tokens):
            pass

        def set_mode(self, mode, tokens):
            pass

        def close(self):
            self.close_calls += 1

    tracker = MarketDataHealthTracker()
    market = MarketData(SimpleNamespace(access_token="token"), Instruments())
    market.live_market_data_health_tracker = tracker
    market._live_market_data_clock = lambda: NOW
    monkeypatch.setattr(market_module, "KiteTicker", Ticker)
    market.connect_live("NIFTY 50")
    ticker = Ticker.latest
    assert market._live_ticker is ticker
    assert market.disconnect_live() is True
    assert ticker.close_calls == 1
    assert market._live_ticker is None
    assert tracker.snapshot(NOW).state is MarketDataHealthState.DISCONNECTED
    assert market.disconnect_live() is False
    assert ticker.close_calls == 1
