from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from trading.candle import Candle


def _freeze_mapping(
    values
):
    """
    Returns a read-only copy of a mapping.
    """

    if not isinstance(
        values,
        Mapping
    ):
        raise TypeError(
            "Values must be a mapping."
        )

    frozen_values = {}

    for key, value in values.items():

        if isinstance(value, Mapping):
            frozen_values[key] = _freeze_mapping(
                value
            )
        else:
            frozen_values[key] = value

    return MappingProxyType(
        frozen_values
    )


class SignalAction(Enum):
    """
    Supported strategy signal actions.
    """

    HOLD = "HOLD"
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class IndicatorSnapshot:
    """
    Immutable candle and indicator view for strategies.
    """

    candle: Candle
    values: Mapping[str, Any]

    def __post_init__(
        self
    ):

        if not isinstance(
            self.candle,
            Candle
        ):
            raise TypeError(
                "Expected Candle object."
            )

        object.__setattr__(
            self,
            "values",
            _freeze_mapping(
                self.values
            )
        )


@dataclass(frozen=True)
class StrategyResult:
    """
    Immutable result of a strategy evaluation.
    """

    strategy_name: str
    action: SignalAction
    candle_time: datetime
    reason: str | None = None
    metadata: Mapping[str, Any] = field(
        default_factory=dict
    )

    def __post_init__(
        self
    ):

        if not isinstance(
            self.action,
            SignalAction
        ):
            raise TypeError(
                "Action must be a SignalAction."
            )

        if not isinstance(
            self.candle_time,
            datetime
        ):
            raise TypeError(
                "Candle time must be a datetime."
            )

        object.__setattr__(
            self,
            "metadata",
            _freeze_mapping(
                self.metadata
            )
        )


class StrategyEngine:
    """
    Evaluates indicator snapshots into strategy results.
    """

    def __init__(
        self,
        strategy_name="base"
    ):

        self.strategy_name = strategy_name

    def evaluate(
        self,
        snapshot: IndicatorSnapshot
    ) -> StrategyResult:
        """
        Evaluates one indicator snapshot.
        """

        if not isinstance(
            snapshot,
            IndicatorSnapshot
        ):
            raise TypeError(
                "Expected IndicatorSnapshot object."
            )

        return StrategyResult(
            strategy_name=self.strategy_name,
            action=SignalAction.HOLD,
            candle_time=snapshot.candle.time,
            reason="No strategy rule configured."
        )
