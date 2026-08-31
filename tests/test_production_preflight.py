from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from core.production_application import ProductionApplication, ProductionApplicationState
from core.production_auth import ProductionAuthenticationState
from core.production_health import ProductionHealthInspector, ProductionHealthState
from core.production_preflight import (
    ProductionPreflightCheck,
    ProductionPreflightInspector,
    ProductionPreflightStatus,
)
from tests.test_production_application import (
    FakeKiteClient,
    authenticator,
    builder,
    live_application,
)
from trading.live_readiness import LiveReadinessState
from trading.execution_mode import ExecutionMode
from trading.market_data_health import MarketDataHealthState
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_manager import PositionManager
from trading.position_store import PositionStore


NOW = datetime.fromisoformat("2026-08-31T09:15:00+05:30")


def check(report, identifier):
    return report.check(identifier).status


def pending_order():
    return {
        "order_id": "pending-1",
        "tradingsymbol": "NIFTY26AUG25000CE",
        "exchange": "NFO",
        "transaction_type": "BUY",
        "quantity": 65,
        "filled_quantity": 0,
        "pending_quantity": 65,
        "average_price": 0.0,
        "product": "MIS",
        "status": "OPEN",
    }


def test_safe_initialized_live_graph_passes_without_side_effects(tmp_path):
    application, client = live_application(tmp_path)
    application.start(NOW)
    runtime = application.components.runtime
    market = application.components.market
    before_calls = (
        client.positions_calls, client.orders_calls, client.order_history_calls,
        client.place_order_calls, client.modify_order_calls, client.cancel_order_calls,
    )
    before_state = (
        application.state, runtime.readiness_gate.state,
        runtime.execution_coordinator.enabled,
        runtime.market_data_health_tracker.snapshot(NOW),
        runtime.position_manager.active_position,
        runtime.closed_position_history.positions,
        market.pending_live_order, market.live_execution_context,
    )

    first = ProductionPreflightInspector().inspect(application)
    second = ProductionPreflightInspector().inspect(application)

    assert first == second
    assert first.status is ProductionPreflightStatus.PASS and first.passed
    assert check(first, "MARKET_DATA_RUNTIME_HEALTH") is (
        ProductionPreflightStatus.NOT_APPLICABLE
    )
    assert runtime.market_data_health_tracker.snapshot(NOW).state is (
        MarketDataHealthState.DISCONNECTED
    )
    assert before_calls == (
        client.positions_calls, client.orders_calls, client.order_history_calls,
        client.place_order_calls, client.modify_order_calls, client.cancel_order_calls,
    )
    assert before_state == (
        application.state, runtime.readiness_gate.state,
        runtime.execution_coordinator.enabled,
        runtime.market_data_health_tracker.snapshot(NOW),
        runtime.position_manager.active_position,
        runtime.closed_position_history.positions,
        market.pending_live_order, market.live_execution_context,
    )
    assert not application.components.position_store.path.exists()
    assert not application.components.closed_position_history_store.path.exists()
    assert not application.components.config.log_directory.exists()


def test_disabled_live_execution_is_reported_without_being_enabled(tmp_path):
    application, _ = live_application(tmp_path, enabled=False)
    application.start(NOW)
    report = ProductionPreflightInspector().inspect(application)
    assert report.passed and report.execution_enabled is False
    assert check(report, "EXECUTION_ENABLEMENT_CONSISTENT") is (
        ProductionPreflightStatus.PASS
    )
    assert application.components.runtime.execution_coordinator.enabled is False


@pytest.mark.parametrize("started", [False, True])
def test_non_initialized_live_application_fails(tmp_path, started):
    application, client = live_application(tmp_path)
    if started:
        application = ProductionApplication(
            builder(tmp_path),
            authenticator(client, state=ProductionAuthenticationState.LOGIN_REQUIRED),
            object(),
        )
        application.start(NOW)
        assert application.authentication_result is not None
    report = ProductionPreflightInspector().inspect(application)
    assert report.status is ProductionPreflightStatus.FAIL
    assert check(report, "APPLICATION_INITIALIZED") is ProductionPreflightStatus.FAIL


def test_pending_recovery_truth_is_consistent_but_cannot_pass_preflight(tmp_path):
    client = FakeKiteClient([pending_order()])
    application = ProductionApplication(
        builder(tmp_path), authenticator(client), object()
    )
    with pytest.raises(Exception):
        application.start(NOW)
    report = ProductionPreflightInspector().inspect(application)
    assert report.status is ProductionPreflightStatus.FAIL
    assert check(report, "RESTART_STATE_SAFE") is ProductionPreflightStatus.FAIL
    assert check(report, "PENDING_ORDER_STATE_CONSISTENT") is (
        ProductionPreflightStatus.PASS
    )
    assert client.place_order_calls == 0


def test_authenticated_client_identity_mismatch_fails_without_secret_output(tmp_path):
    application, _ = live_application(tmp_path)
    application.start(NOW)
    application.components.market.kite = FakeKiteClient()
    secret = application.authentication_result.kite_client.access_token
    report = ProductionPreflightInspector().inspect(application)
    assert check(report, "AUTHENTICATED_CLIENT_IDENTITY") is (
        ProductionPreflightStatus.FAIL
    )
    representations = (repr(report), str(report), repr(report.to_dict()), report.to_json())
    assert all(secret not in value for value in representations)


@pytest.mark.parametrize(
    "mutation,identifier",
    [
        ("manager", "POSITION_MANAGER_IDENTITY"),
        ("risk", "RISK_IDENTITY"),
        ("audit", "AUDIT_IDENTITY"),
        ("position_store", "POSITION_STORE_IDENTITY"),
        ("history_store", "HISTORY_STORE_IDENTITY"),
        ("risk_pnl", "RISK_PNL_CONFIGURATION"),
        ("audit_path", "AUDIT_PATH_CONSISTENT"),
    ],
)
def test_authority_and_store_identity_mismatches_fail(tmp_path, mutation, identifier):
    application, _ = live_application(tmp_path)
    application.start(NOW)
    market = application.components.market
    if mutation == "manager":
        market.live_position_manager = PositionManager()
    elif mutation == "risk":
        market.live_risk_evaluator = None
    elif mutation == "audit":
        market.live_audit_sink = None
    elif mutation == "position_store":
        market.live_position_store = PositionStore(tmp_path / "other-position.json")
    elif mutation == "risk_pnl":
        runtime = application.components.runtime
        runtime.risk_evaluator._session_net_pnl_aggregator = object()
    elif mutation == "audit_path":
        application.components.runtime.audit_sink.path = tmp_path / "other-audit.jsonl"
    else:
        from trading.closed_position_history_store import ClosedPositionHistoryStore
        market.live_closed_position_history_store = ClosedPositionHistoryStore(
            tmp_path / "other-history.json"
        )
    report = ProductionPreflightInspector().inspect(application)
    assert report.status is ProductionPreflightStatus.FAIL
    assert check(report, identifier) is ProductionPreflightStatus.FAIL


def test_position_context_and_store_path_inconsistencies_fail(tmp_path):
    application, _ = live_application(tmp_path)
    application.start(NOW)
    runtime = application.components.runtime
    runtime.position_manager.register(ManagedPosition(
        PositionSide.CE, "NIFTY26AUG25000CE", 65, 100.0, NOW,
        PositionState.OPEN,
    ))
    object.__setattr__(
        runtime.position_store, "path", tmp_path / "wrong-position.json"
    )
    report = ProductionPreflightInspector().inspect(application)
    assert check(report, "POSITION_CONTEXT_CONSISTENT") is ProductionPreflightStatus.FAIL
    assert check(report, "POSITION_STORE_PATH_CONSISTENT") is (
        ProductionPreflightStatus.FAIL
    )


def test_real_restored_position_graph_passes(tmp_path):
    symbol = "NIFTY26AUG25000CE"
    client = FakeKiteClient()

    def positions():
        client.positions_calls += 1
        return {"net": [{
            "tradingsymbol": symbol, "exchange": "NFO", "quantity": 65,
            "average_price": 101.0, "product": "MIS",
        }], "day": []}

    client.positions = positions
    app_builder = builder(tmp_path)
    store = PositionStore(app_builder.config.position_store_path)
    manager = PositionManager()
    manager.register(ManagedPosition(
        PositionSide.CE, symbol, 65, 100.0, NOW, PositionState.OPEN
    ))
    store.save(manager)
    application = ProductionApplication(app_builder, authenticator(client), object())
    application.start(NOW)
    report = ProductionPreflightInspector().inspect(application)
    assert report.passed
    assert application.components.runtime.position_manager.active_position is not None


def test_initialized_paper_passes_with_live_checks_not_applicable(tmp_path):
    application = ProductionApplication(
        builder(tmp_path, mode=ExecutionMode.PAPER, enabled=False)
    )
    application.start()
    report = ProductionPreflightInspector().inspect(application)
    assert report.passed
    assert report.execution_enabled is False
    assert check(report, "APPLICATION_INITIALIZED") is ProductionPreflightStatus.PASS
    assert check(report, "AUTHENTICATION_RESULT_PRESENT") is (
        ProductionPreflightStatus.NOT_APPLICABLE
    )
    assert check(report, "MARKET_DATA_RUNTIME_HEALTH") is (
        ProductionPreflightStatus.NOT_APPLICABLE
    )


def test_models_are_frozen_and_health_inspector_semantics_are_unchanged(tmp_path):
    application, _ = live_application(tmp_path)
    application.start(NOW)
    item = ProductionPreflightCheck(
        "EXAMPLE", ProductionPreflightStatus.PASS, "Safe fixed message."
    )
    with pytest.raises(FrozenInstanceError):
        item.message = "changed"
    health = ProductionHealthInspector(
        application.components.runtime, application.components.market
    ).inspect(NOW)
    assert health.state is ProductionHealthState.NOT_READY
    assert health.market_data_state is MarketDataHealthState.DISCONNECTED
    assert health.readiness_state is LiveReadinessState.READY
