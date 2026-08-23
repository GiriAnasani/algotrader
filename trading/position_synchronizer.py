"""Side-effect-free comparison of managed and broker position state."""

from dataclasses import dataclass
from enum import Enum
import re

from trading.broker_position import BrokerPosition
from trading.position import ManagedPosition
from trading.position_manager import PositionManager


class PositionSynchronizationState(Enum):
    """Outcomes of comparing single-position managed and broker exposure."""

    SYNCHRONIZED_FLAT = "SYNCHRONIZED_FLAT"
    SYNCHRONIZED_OPEN = "SYNCHRONIZED_OPEN"
    MANAGED_ONLY = "MANAGED_ONLY"
    BROKER_ONLY = "BROKER_ONLY"
    MISMATCH = "MISMATCH"


@dataclass(frozen=True)
class PositionSynchronizationResult:
    """Immutable diagnostic result containing the compared object references."""

    state: PositionSynchronizationState
    managed_position: ManagedPosition | None
    broker_positions: tuple[BrokerPosition, ...]
    message: str

    def __post_init__(self):
        if not isinstance(self.state, PositionSynchronizationState):
            raise TypeError("State must be a PositionSynchronizationState.")
        if self.managed_position is not None and not isinstance(
            self.managed_position, ManagedPosition
        ):
            raise TypeError("Managed position must be a ManagedPosition or None.")
        if not isinstance(self.broker_positions, tuple):
            raise TypeError("Broker positions must be a tuple.")
        if not all(
            isinstance(position, BrokerPosition) for position in self.broker_positions
        ):
            raise TypeError("Broker positions must contain BrokerPosition values.")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("Message must be a non-empty string.")

        object.__setattr__(self, "message", self.message.strip())

    @property
    def is_synchronized(self):
        return self.state in (
            PositionSynchronizationState.SYNCHRONIZED_FLAT,
            PositionSynchronizationState.SYNCHRONIZED_OPEN,
        )


class PositionSynchronizer:
    """Compare an already-read broker snapshot without changing any state."""

    _NIFTY_OPTION_SYMBOL = re.compile(r"^NIFTY\d.*(?:CE|PE)$", re.IGNORECASE)

    def __init__(self, position_manager):
        if not isinstance(position_manager, PositionManager):
            raise TypeError("Position manager must be a PositionManager.")
        self._position_manager = position_manager

    def synchronize(self, broker_positions):
        """Return a diagnostic comparison; this method performs no repair."""
        positions = self._validate_snapshot(broker_positions)
        relevant = tuple(position for position in positions if self._is_relevant(position))
        managed = self._position_manager.active_position

        if len(relevant) > 1:
            return self._result(
                PositionSynchronizationState.MISMATCH,
                managed,
                relevant,
                "Multiple relevant broker exposures are incompatible with one managed position.",
            )
        if managed is None:
            if not relevant:
                return self._result(
                    PositionSynchronizationState.SYNCHRONIZED_FLAT,
                    None,
                    (),
                    "Managed and broker position state are both flat.",
                )
            return self._result(
                PositionSynchronizationState.BROKER_ONLY,
                None,
                relevant,
                "Broker exposure exists while managed position state is flat.",
            )
        if not relevant:
            return self._result(
                PositionSynchronizationState.MANAGED_ONLY,
                managed,
                (),
                "Managed exposure exists without relevant broker exposure.",
            )

        broker = relevant[0]
        symbol_matches = broker.tradingsymbol.upper() == managed.contract_symbol.upper()
        quantity_matches = broker.quantity == managed.quantity
        side_matches = broker.tradingsymbol.upper().endswith(managed.side.value)
        if symbol_matches and quantity_matches and side_matches:
            return self._result(
                PositionSynchronizationState.SYNCHRONIZED_OPEN,
                managed,
                relevant,
                "Managed and broker open exposure agree.",
            )
        return self._result(
            PositionSynchronizationState.MISMATCH,
            managed,
            relevant,
            "Managed and broker exposure disagree on side, symbol, or quantity.",
        )

    @staticmethod
    def _validate_snapshot(broker_positions):
        if not isinstance(broker_positions, (list, tuple)):
            raise TypeError("Broker positions must be a list or tuple.")
        if not all(isinstance(position, BrokerPosition) for position in broker_positions):
            raise TypeError("Broker positions must contain BrokerPosition values.")
        return tuple(broker_positions)

    @classmethod
    def _is_relevant(cls, position):
        return (
            position.quantity != 0
            and position.exchange.upper() == "NFO"
            and cls._NIFTY_OPTION_SYMBOL.fullmatch(position.tradingsymbol) is not None
        )

    @staticmethod
    def _result(state, managed_position, broker_positions, message):
        return PositionSynchronizationResult(
            state=state,
            managed_position=managed_position,
            broker_positions=tuple(broker_positions),
            message=message,
        )
