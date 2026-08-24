import json
from dataclasses import FrozenInstanceError, fields
from enum import Enum
import math
from pathlib import Path

import pytest

from core.production_config import DeploymentEnvironment, ProductionConfig
from trading.execution_mode import ExecutionMode


def config(**overrides):
    values = {
        "deployment_environment": DeploymentEnvironment.LOCAL,
        "execution_mode": ExecutionMode.PAPER,
        "execution_enabled": False,
        "position_store_path": str(Path("state/position.json")),
        "closed_position_history_store_path": str(Path("state/closed.json")),
        "log_directory": str(Path("var/log")),
        "strategy_target_points": 2.0,
    }
    values.update(overrides)
    return ProductionConfig(**values)


def test_deployment_environment_has_exact_values():
    assert issubclass(DeploymentEnvironment, Enum)
    assert [(item.name, item.value) for item in DeploymentEnvironment] == [
        ("LOCAL", "LOCAL"),
        ("PRODUCTION", "PRODUCTION"),
    ]


@pytest.mark.parametrize("value", [None, "LOCAL", object(), True])
def test_invalid_deployment_environment_is_rejected(value):
    with pytest.raises(TypeError):
        config(deployment_environment=value)


def test_model_is_frozen_and_has_exact_fields():
    value = config()
    assert [field.name for field in fields(value)] == [
        "deployment_environment",
        "execution_mode",
        "execution_enabled",
        "position_store_path",
        "closed_position_history_store_path",
        "log_directory",
        "strategy_target_points",
    ]
    with pytest.raises(FrozenInstanceError):
        value.execution_enabled = True


@pytest.mark.parametrize(
    "environment, mode, enabled",
    [
        (DeploymentEnvironment.LOCAL, ExecutionMode.PAPER, False),
        (DeploymentEnvironment.LOCAL, ExecutionMode.LIVE, False),
        (DeploymentEnvironment.PRODUCTION, ExecutionMode.PAPER, False),
        (DeploymentEnvironment.PRODUCTION, ExecutionMode.LIVE, False),
        (DeploymentEnvironment.PRODUCTION, ExecutionMode.LIVE, True),
    ],
)
def test_supported_configuration_dimensions_are_independent(
    environment, mode, enabled
):
    value = config(
        deployment_environment=environment,
        execution_mode=mode,
        execution_enabled=enabled,
    )
    assert value.deployment_environment is environment
    assert value.execution_mode is mode
    assert value.execution_enabled is enabled


def test_paper_with_execution_enabled_is_rejected():
    with pytest.raises(ValueError):
        config(execution_enabled=True)


@pytest.mark.parametrize("value", [None, "PAPER", object(), True])
def test_exact_existing_execution_mode_is_required(value):
    with pytest.raises(TypeError):
        config(execution_mode=value)


@pytest.mark.parametrize("value", [None, 0, 1, "false", object()])
def test_execution_enablement_requires_strict_bool(value):
    with pytest.raises(TypeError):
        config(execution_mode=ExecutionMode.LIVE, execution_enabled=value)


def test_safe_local_default_is_disabled_paper_with_stable_target():
    value = ProductionConfig.safe_local_default()
    assert value.deployment_environment is DeploymentEnvironment.LOCAL
    assert value.execution_mode is ExecutionMode.PAPER
    assert value.execution_enabled is False
    assert value.strategy_target_points == 2.0


def test_production_paper_factory_is_disabled():
    value = ProductionConfig.production_paper()
    assert value.deployment_environment is DeploymentEnvironment.PRODUCTION
    assert value.execution_mode is ExecutionMode.PAPER
    assert value.execution_enabled is False


def test_production_live_factory_defaults_disabled_and_requires_explicit_enablement():
    disabled = ProductionConfig.production_live()
    enabled = ProductionConfig.production_live(execution_enabled=True)
    assert disabled.deployment_environment is DeploymentEnvironment.PRODUCTION
    assert disabled.execution_mode is ExecutionMode.LIVE
    assert disabled.execution_enabled is False
    assert enabled.execution_enabled is True


@pytest.mark.parametrize("field_name", [
    "position_store_path",
    "closed_position_history_store_path",
    "log_directory",
])
def test_string_and_path_inputs_are_normalized_to_path(field_name):
    assert isinstance(
        getattr(config(**{field_name: "not-created/value"}), field_name), Path
    )
    supplied = Path("another/not-created/value")
    assert getattr(config(**{field_name: supplied}), field_name) == supplied


@pytest.mark.parametrize("field_name", [
    "position_store_path",
    "closed_position_history_store_path",
    "log_directory",
])
@pytest.mark.parametrize("value", ["", "   ", None, True, object()])
def test_invalid_paths_are_rejected(field_name, value):
    with pytest.raises((TypeError, ValueError)):
        config(**{field_name: value})


def test_config_creation_does_not_require_or_create_paths(tmp_path):
    paths = [
        tmp_path / "missing" / "position.json",
        tmp_path / "missing" / "closed.json",
        tmp_path / "logs",
    ]
    value = config(
        position_store_path=paths[0],
        closed_position_history_store_path=paths[1],
        log_directory=paths[2],
    )
    assert value.position_store_path == paths[0]
    assert all(not path.exists() for path in paths)


@pytest.mark.parametrize("target", [0.25, 2, 17.5])
def test_positive_finite_target_points_are_accepted(target):
    assert config(strategy_target_points=target).strategy_target_points == target


@pytest.mark.parametrize(
    "target", [0, -1, True, None, "2", math.nan, math.inf, -math.inf]
)
def test_invalid_target_points_are_rejected(target):
    with pytest.raises((TypeError, ValueError)):
        config(strategy_target_points=target)


def test_to_dict_has_exact_serialized_values_and_is_fresh():
    value = config(execution_mode=ExecutionMode.LIVE, execution_enabled=True)
    expected = {
        "deployment_environment": "LOCAL",
        "execution_mode": "LIVE",
        "execution_enabled": True,
        "position_store_path": str(Path("state/position.json")),
        "closed_position_history_store_path": str(Path("state/closed.json")),
        "log_directory": str(Path("var/log")),
        "strategy_target_points": 2.0,
    }
    first = value.to_dict()
    second = value.to_dict()
    assert first == expected
    assert second == expected
    assert first is not second
    first["execution_enabled"] = False
    assert value.execution_enabled is True
    assert value.to_dict() == expected


def test_to_json_is_deterministic_and_matches_dict():
    value = config()
    assert value.to_json() == value.to_json()
    assert json.loads(value.to_json()) == value.to_dict()


def test_configuration_module_preserves_phase_boundaries():
    source = Path("core/production_config.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "getenv(", "dotenv", "boto3", "api_key", "api_secret", "access_token",
        "password", "kiteconnect", "place_order", ".save(", ".load(",
        "basicconfig", "static_ip", "static-ip", "daily_loss", "max_trades",
    )
    assert all(term not in source for term in forbidden)
