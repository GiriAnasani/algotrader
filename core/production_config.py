"""Infrastructure-neutral production configuration domain."""

from dataclasses import dataclass
from enum import Enum
import json
import math
from numbers import Real
import os
from pathlib import Path

from trading.execution_mode import ExecutionMode


class DeploymentEnvironment(Enum):
    """Supported application deployment environments."""

    LOCAL = "LOCAL"
    PRODUCTION = "PRODUCTION"


def _normalized_path(value, field_name):
    if isinstance(value, bool) or not isinstance(value, (str, os.PathLike)):
        raise TypeError(f"{field_name} must be a string or path-like value.")
    raw_path = os.fspath(value)
    if not isinstance(raw_path, str):
        raise TypeError(f"{field_name} must resolve to a string path.")
    if not raw_path.strip():
        raise ValueError(f"{field_name} must not be empty.")
    return Path(raw_path)


@dataclass(frozen=True)
class ProductionConfig:
    """Immutable startup configuration with independent safety dimensions."""

    deployment_environment: DeploymentEnvironment
    execution_mode: ExecutionMode
    execution_enabled: bool
    position_store_path: Path
    closed_position_history_store_path: Path
    log_directory: Path
    strategy_target_points: Real = 2.0

    def __post_init__(self):
        if not isinstance(self.deployment_environment, DeploymentEnvironment):
            raise TypeError(
                "deployment_environment must be a DeploymentEnvironment."
            )
        if not isinstance(self.execution_mode, ExecutionMode):
            raise TypeError("execution_mode must be an ExecutionMode.")
        if not isinstance(self.execution_enabled, bool):
            raise TypeError("execution_enabled must be a bool.")
        if self.execution_mode is ExecutionMode.PAPER and self.execution_enabled:
            raise ValueError("PAPER execution cannot be enabled.")

        for field_name in (
            "position_store_path",
            "closed_position_history_store_path",
            "log_directory",
        ):
            object.__setattr__(
                self,
                field_name,
                _normalized_path(getattr(self, field_name), field_name),
            )

        target = self.strategy_target_points
        if isinstance(target, bool) or not isinstance(target, Real):
            raise TypeError("strategy_target_points must be a real number.")
        if not math.isfinite(target) or target <= 0:
            raise ValueError("strategy_target_points must be a positive finite number.")

    @classmethod
    def safe_local_default(cls):
        """Returns the safe local PAPER configuration."""
        return cls(
            deployment_environment=DeploymentEnvironment.LOCAL,
            execution_mode=ExecutionMode.PAPER,
            execution_enabled=False,
            position_store_path=Path("data/runtime/position.json"),
            closed_position_history_store_path=Path(
                "data/runtime/closed_positions.json"
            ),
            log_directory=Path("logs"),
        )

    @classmethod
    def production_paper(
        cls,
        position_store_path="data/runtime/position.json",
        closed_position_history_store_path="data/runtime/closed_positions.json",
        log_directory="logs",
        strategy_target_points=2.0,
    ):
        """Returns a disabled PRODUCTION PAPER configuration."""
        return cls(
            DeploymentEnvironment.PRODUCTION,
            ExecutionMode.PAPER,
            False,
            position_store_path,
            closed_position_history_store_path,
            log_directory,
            strategy_target_points,
        )

    @classmethod
    def production_live(
        cls,
        position_store_path="data/runtime/position.json",
        closed_position_history_store_path="data/runtime/closed_positions.json",
        log_directory="logs",
        strategy_target_points=2.0,
        execution_enabled=False,
    ):
        """Returns a PRODUCTION LIVE configuration with explicit enablement."""
        return cls(
            DeploymentEnvironment.PRODUCTION,
            ExecutionMode.LIVE,
            execution_enabled,
            position_store_path,
            closed_position_history_store_path,
            log_directory,
            strategy_target_points,
        )

    def to_dict(self):
        """Returns a fresh, serializable configuration snapshot."""
        return {
            "deployment_environment": self.deployment_environment.value,
            "execution_mode": self.execution_mode.value,
            "execution_enabled": self.execution_enabled,
            "position_store_path": str(self.position_store_path),
            "closed_position_history_store_path": str(
                self.closed_position_history_store_path
            ),
            "log_directory": str(self.log_directory),
            "strategy_target_points": self.strategy_target_points,
        }

    def to_json(self):
        """Returns a deterministic JSON representation without filesystem I/O."""
        return json.dumps(
            self.to_dict(), allow_nan=False, sort_keys=True, separators=(",", ":")
        )
