from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from numbers import Real
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
    BUY_CE = "BUY_CE"
    BUY_PE = "BUY_PE"
    EXIT_CE = "EXIT_CE"
    EXIT_PE = "EXIT_PE"


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
    actions: tuple[SignalAction, ...] = ()

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

        if not self.actions:
            object.__setattr__(
                self,
                "actions",
                (self.action,)
            )

        if any(
            not isinstance(action, SignalAction)
            for action in self.actions
        ):
            raise TypeError(
                "Actions must contain SignalAction values."
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
        strategy_name="nifty_ema10_crossover",
        target_points=5.0
    ):

        self.strategy_name = strategy_name
        self.target_points = target_points
        self.previous_close = None
        self.previous_ema10 = None
        self.active_position = None
        self.entry_premium = None

    def evaluate(
        self,
        snapshot: IndicatorSnapshot,
        option_premiums=None
    ) -> StrategyResult:
        """
        Evaluates one completed NIFTY candle snapshot.
        """

        if not isinstance(
            snapshot,
            IndicatorSnapshot
        ):
            raise TypeError(
                "Expected IndicatorSnapshot object."
            )

        premiums = self._validate_option_premiums(
            option_premiums
        )

        ema10 = snapshot.values.get(
            "ema",
            {}
        ).get(10)

        if ema10 is None:
            return self._hold(
                snapshot,
                "EMA 10 is not available."
            )

        close = snapshot.candle.close

        if self.active_position == "CE":
            result = self._evaluate_active_ce(
                snapshot,
                ema10,
                premiums
            )
        elif self.active_position == "PE":
            result = self._evaluate_active_pe(
                snapshot,
                ema10,
                premiums
            )
        else:
            result = self._evaluate_entry(
                snapshot,
                ema10,
                premiums
            )

        self.previous_close = close
        self.previous_ema10 = ema10

        return result

    def evaluate_live_option_target(
        self,
        snapshot: IndicatorSnapshot,
        option_premiums=None
    ) -> StrategyResult | None:
        """
        Checks the existing option target without evaluating EMA rules.

        This is used by live option ticks.  EMA entry and reversal
        decisions remain exclusive to completed NIFTY candles.
        """

        if not isinstance(
            snapshot,
            IndicatorSnapshot
        ):
            raise TypeError(
                "Expected IndicatorSnapshot object."
            )

        premiums = self._validate_option_premiums(
            option_premiums
        )

        if self.active_position == "CE":
            if self._target_reached(premiums.get("CE")):
                self._clear_position()
                return self._result(
                    snapshot,
                    SignalAction.EXIT_CE,
                    "CE target reached."
                )

        elif self.active_position == "PE":
            if self._target_reached(premiums.get("PE")):
                self._clear_position()
                return self._result(
                    snapshot,
                    SignalAction.EXIT_PE,
                    "PE target reached."
                )

        return None

    def _validate_option_premiums(
        self,
        option_premiums
    ):
        """
        Validates injected CE and PE option premiums.
        """

        if option_premiums is None:
            return {}

        if not isinstance(
            option_premiums,
            Mapping
        ):
            raise TypeError(
                "Option premiums must be a mapping."
            )

        premiums = {}

        for option_type, premium in option_premiums.items():

            if option_type not in ("CE", "PE"):
                raise ValueError(
                    "Option premium type must be CE or PE."
                )

            if not isinstance(premium, Real):
                raise TypeError(
                    "Option premium must be a number."
                )

            if premium <= 0:
                raise ValueError(
                    "Option premium must be greater than zero."
                )

            premiums[option_type] = float(premium)

        return premiums

    def _evaluate_entry(
        self,
        snapshot,
        ema10,
        premiums
    ):
        """
        Evaluates EMA-10 crossover entries.
        """

        if self.previous_close is None:
            return self._hold(
                snapshot,
                "Previous EMA 10 candle is not available."
            )

        bullish_crossover = (
            self.previous_close <= self.previous_ema10
            and snapshot.candle.close > ema10
        )

        bearish_crossover = (
            self.previous_close >= self.previous_ema10
            and snapshot.candle.close < ema10
        )

        if bullish_crossover:
            return self._enter_position(
                snapshot,
                "CE",
                premiums
            )

        if bearish_crossover:
            return self._enter_position(
                snapshot,
                "PE",
                premiums
            )

        return self._hold(
            snapshot,
            "No EMA 10 crossover."
        )

    def _evaluate_active_ce(
        self,
        snapshot,
        ema10,
        premiums
    ):
        """
        Evaluates CE target and reversal exits.
        """

        if self._target_reached(
            premiums.get("CE")
        ):
            self._clear_position()
            return self._result(
                snapshot,
                SignalAction.EXIT_CE,
                "CE target reached."
            )

        if snapshot.candle.close < ema10:
            self._clear_position()
            return self._exit_and_reverse(
                snapshot,
                SignalAction.EXIT_CE,
                "PE",
                premiums
            )

        return self._hold(
            snapshot,
            "CE position remains active."
        )

    def _evaluate_active_pe(
        self,
        snapshot,
        ema10,
        premiums
    ):
        """
        Evaluates PE target and reversal exits.
        """

        if self._target_reached(
            premiums.get("PE")
        ):
            self._clear_position()
            return self._result(
                snapshot,
                SignalAction.EXIT_PE,
                "PE target reached."
            )

        if snapshot.candle.close > ema10:
            self._clear_position()
            return self._exit_and_reverse(
                snapshot,
                SignalAction.EXIT_PE,
                "CE",
                premiums
            )

        return self._hold(
            snapshot,
            "PE position remains active."
        )

    def _enter_position(
        self,
        snapshot,
        option_type,
        premiums
    ):
        """
        Records a generated CE or PE entry.
        """

        entry_premium = premiums.get(
            option_type
        )

        if entry_premium is None:
            return self._hold(
                snapshot,
                f"{option_type} premium is not available."
            )

        self.active_position = option_type
        self.entry_premium = entry_premium

        action = (
            SignalAction.BUY_CE
            if option_type == "CE"
            else SignalAction.BUY_PE
        )

        return self._result(
            snapshot,
            action,
            f"EMA 10 {option_type} entry."
        )

    def _exit_and_reverse(
        self,
        snapshot,
        exit_action,
        option_type,
        premiums
    ):
        """
        Exits an active position and enters the reversal when priced.
        """

        entry_result = self._enter_position(
            snapshot,
            option_type,
            premiums
        )

        if entry_result.action is SignalAction.HOLD:
            return self._result(
                snapshot,
                exit_action,
                f"EMA 10 reversal exit; {option_type} premium is not available."
            )

        return self._result(
            snapshot,
            exit_action,
            f"EMA 10 reversal exit and {option_type} entry.",
            actions=(exit_action, entry_result.action)
        )

    def _target_reached(
        self,
        premium
    ):
        """
        Returns whether the active option reached its target.
        """

        return (
            premium is not None
            and premium >= self.entry_premium + self.target_points
        )

    def _clear_position(
        self
    ):
        """
        Clears the active option position state.
        """

        self.active_position = None
        self.entry_premium = None

    def _hold(
        self,
        snapshot,
        reason
    ):
        """
        Returns a HOLD result.
        """

        return self._result(
            snapshot,
            SignalAction.HOLD,
            reason
        )

    def _result(
        self,
        snapshot,
        action,
        reason,
        actions=None
    ):
        """
        Creates a strategy result.
        """

        return StrategyResult(
            strategy_name=self.strategy_name,
            action=action,
            candle_time=snapshot.candle.time,
            reason=reason,
            actions=actions or (action,)
        )
