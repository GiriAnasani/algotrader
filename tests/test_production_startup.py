from dataclasses import FrozenInstanceError, fields
import inspect
from pathlib import Path

import pytest

from core.production_config import DeploymentEnvironment, ProductionConfig
from core.production_startup import (
    ProductionStartupBuilder,
    ProductionStartupComponents,
)
from trading.execution_mode import ExecutionMode
from trading.live_readiness import LiveReadinessState
from trading.market import MarketData
from trading.live_risk import LiveRiskLimits
from trading.option_charges import (
    NetPnLCalculator,
    OptionTradeChargesCalculator,
    ZERODHA_NSE_OPTIONS_2026,
)
from trading.realized_net_pnl import RealizedNetPnLAggregator
from trading.session_net_pnl import SessionNetPnLAggregator


class FakeKiteClient:
    def __init__(self):
        self.positions_calls = 0
        self.orders_calls = 0
        self.order_history_calls = 0
        self.place_order_calls = 0
        self.modify_order_calls = 0
        self.cancel_order_calls = 0
        self.websocket_calls = 0

    def positions(self):
        self.positions_calls += 1

    def orders(self):
        self.orders_calls += 1

    def order_history(self, order_id):
        self.order_history_calls += 1

    def place_order(self, **kwargs):
        self.place_order_calls += 1

    def modify_order(self, **kwargs):
        self.modify_order_calls += 1

    def cancel_order(self, **kwargs):
        self.cancel_order_calls += 1

    def start_websocket(self):
        self.websocket_calls += 1


def make_config(tmp_path, environment=DeploymentEnvironment.LOCAL,
                mode=ExecutionMode.PAPER, enabled=False, target=2.0):
    return ProductionConfig(
        deployment_environment=environment,
        execution_mode=mode,
        execution_enabled=enabled,
        position_store_path=tmp_path / "state" / "position.json",
        closed_position_history_store_path=tmp_path / "history" / "closed.json",
        log_directory=tmp_path / "logs",
        strategy_target_points=target,
    )


def risk_limits():
    return LiveRiskLimits(1, 65, 10, 1000.0)


def risk_pnl():
    return SessionNetPnLAggregator(
        RealizedNetPnLAggregator(
            NetPnLCalculator(
                OptionTradeChargesCalculator(ZERODHA_NSE_OPTIONS_2026)
            )
        )
    )


def builder(config):
    return ProductionStartupBuilder(config, risk_limits(), risk_pnl())


def assert_no_calls(client):
    assert client.positions_calls == 0
    assert client.orders_calls == 0
    assert client.order_history_calls == 0
    assert client.place_order_calls == 0
    assert client.modify_order_calls == 0
    assert client.cancel_order_calls == 0
    assert client.websocket_calls == 0


@pytest.mark.parametrize("value", [None, {}, "config", object(), True])
def test_builder_requires_exact_production_config(value):
    with pytest.raises(TypeError):
        ProductionStartupBuilder(value)


def test_builder_and_result_retain_exact_config_and_are_immutable(tmp_path):
    config = make_config(tmp_path)
    builder = ProductionStartupBuilder(config)
    result = builder.build()
    assert builder.config is config
    assert result.config is config
    assert [field.name for field in fields(result)] == ["config", "runtime", "market"]
    with pytest.raises(FrozenInstanceError):
        result.config = make_config(tmp_path)


@pytest.mark.parametrize(
    "environment",
    [DeploymentEnvironment.LOCAL, DeploymentEnvironment.PRODUCTION],
)
def test_paper_build_is_minimal_disabled_and_environment_neutral(
    tmp_path, environment
):
    config = make_config(tmp_path, environment=environment)
    result = ProductionStartupBuilder(config).build()
    assert result.execution_mode is ExecutionMode.PAPER
    assert result.execution_enabled is False
    assert result.runtime is None
    assert isinstance(result.market, MarketData)
    assert result.market.execution_router.mode is ExecutionMode.PAPER
    assert result.market.live_execution_coordinator is None
    assert result.market.live_readiness_gate is None
    assert result.market.live_position_manager is None
    assert result.position_store is None
    assert result.closed_position_history_store is None


def test_paper_requires_no_client_and_makes_no_calls_when_one_is_supplied(tmp_path):
    config = make_config(tmp_path)
    ProductionStartupBuilder(config).build()
    client = FakeKiteClient()
    result = ProductionStartupBuilder(config).build(client, object())
    assert result.market.kite is client
    assert_no_calls(client)


@pytest.mark.parametrize(
    "missing",
    ["kite_client", "instruments"],
)
def test_live_requires_explicit_inputs(tmp_path, missing):
    config = make_config(tmp_path, mode=ExecutionMode.LIVE)
    arguments = {"kite_client": FakeKiteClient(), "instruments": object()}
    arguments[missing] = None
    with pytest.raises(ValueError, match="LIVE startup requires"):
        builder(config).build(**arguments)


def test_live_requires_explicit_risk_limits(tmp_path):
    config = make_config(tmp_path, mode=ExecutionMode.LIVE)
    with pytest.raises(ValueError, match="explicit live risk limits"):
        ProductionStartupBuilder(config).build(FakeKiteClient(), object())


def test_live_requires_explicit_risk_pnl_dependency(tmp_path):
    config = make_config(tmp_path, mode=ExecutionMode.LIVE)
    with pytest.raises(ValueError, match="risk session net P&L aggregator"):
        ProductionStartupBuilder(config, risk_limits()).build(
            FakeKiteClient(), object()
        )


@pytest.mark.parametrize(
    "environment, enabled",
    [
        (DeploymentEnvironment.LOCAL, False),
        (DeploymentEnvironment.LOCAL, True),
        (DeploymentEnvironment.PRODUCTION, False),
        (DeploymentEnvironment.PRODUCTION, True),
    ],
)
def test_live_build_propagates_only_configured_enablement_without_activity(
    tmp_path, environment, enabled
):
    config = make_config(
        tmp_path,
        environment=environment,
        mode=ExecutionMode.LIVE,
        enabled=enabled,
    )
    client = FakeKiteClient()
    instruments = object()
    result = builder(config).build(client, instruments)
    assert result.config is config
    assert result.execution_mode is ExecutionMode.LIVE
    assert result.execution_enabled is enabled
    assert result.runtime.execution_coordinator.enabled is enabled
    assert result.runtime.risk_evaluator.limits is not None
    assert result.market.live_risk_evaluator is result.runtime.risk_evaluator
    assert result.market.live_risk_guard is result.runtime.risk_guard
    assert result.market.live_audit_sink is result.runtime.audit_sink
    assert result.runtime.audit_sink.path == config.log_directory / "audit.jsonl"
    assert result.market.kite is client
    assert result.market.instruments is instruments
    assert result.runtime.readiness_gate.state is LiveReadinessState.NOT_READY
    assert result.runtime.readiness_gate.last_recovery_result is None
    assert_no_calls(client)


def test_live_uses_exact_configured_stores_and_shared_runtime_authorities(tmp_path):
    config = make_config(tmp_path, mode=ExecutionMode.LIVE)
    result = builder(config).build(FakeKiteClient(), object())
    runtime = result.runtime
    market = result.market
    assert result.position_store is runtime.position_store
    assert result.position_store.path == config.position_store_path
    assert result.closed_position_history_store is runtime.closed_position_history_store
    assert result.closed_position_history_store.path == (
        config.closed_position_history_store_path
    )
    assert market.live_position_store is runtime.position_store
    assert market.live_closed_position_history_store is runtime.closed_position_history_store
    assert market.live_position_manager is runtime.position_manager
    assert market.live_closed_position_history is runtime.closed_position_history


@pytest.mark.parametrize("mode", [ExecutionMode.PAPER, ExecutionMode.LIVE])
def test_construction_creates_no_configured_files_or_directories(tmp_path, mode):
    config = make_config(tmp_path, mode=mode)
    arguments = () if mode is ExecutionMode.PAPER else (FakeKiteClient(), object())
    (ProductionStartupBuilder(config) if mode is ExecutionMode.PAPER else builder(config)).build(*arguments)
    assert not config.position_store_path.exists()
    assert not config.closed_position_history_store_path.exists()
    assert not config.log_directory.exists()
    assert list(tmp_path.iterdir()) == []


def test_custom_target_is_retained_without_mutating_current_strategy(tmp_path):
    config = make_config(tmp_path, target=7.5)
    result = ProductionStartupBuilder(config).build()
    assert result.config.strategy_target_points == 7.5
    assert result.market.strategy_engine.target_points == 2.0


def test_result_rejects_mismatched_mode(tmp_path):
    config = make_config(tmp_path, mode=ExecutionMode.LIVE)
    paper_market = MarketData(None, None)
    with pytest.raises(TypeError):
        ProductionStartupComponents(config, None, paper_market)


def test_builder_is_composition_only_and_preserves_phase_boundaries():
    source = inspect.getsource(ProductionStartupBuilder).lower()
    forbidden = (
        "initialize_live_session", ".initialize(", ".recover(", ".restore(",
        ".load(", ".save(", "positions(", "orders(", "order_history(",
        "place_order(", "modify_order(", "cancel_order(", "connect(",
        "getenv(", "dotenv", "boto3", "api_key", "api_secret", "access_token",
        "password", "static_ip", "daily_loss", "max_trades", "basicconfig",
    )
    assert all(term not in source for term in forbidden)
