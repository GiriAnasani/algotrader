"""Deterministic aggregation of current open-position gross P&L."""

from dataclasses import dataclass
from math import isfinite
from numbers import Real

from trading.position import ManagedPosition
from trading.position_pnl import PositionPnLCalculator


@dataclass(frozen=True)
class UnrealizedPnLResult:
    """Immutable gross P&L summary for current managed position truth."""

    position_count: int
    total_gross_pnl: float

    def __post_init__(self):
        if (
            isinstance(self.position_count, bool)
            or not isinstance(self.position_count, int)
            or self.position_count not in (0, 1)
        ):
            raise ValueError("Position count must be zero or one.")
        if (
            isinstance(self.total_gross_pnl, bool)
            or not isinstance(self.total_gross_pnl, Real)
            or not isfinite(self.total_gross_pnl)
        ):
            raise ValueError("Total gross P&L must be a finite number.")
        if self.position_count == 0 and self.total_gross_pnl != 0:
            raise ValueError("Flat position state requires zero gross P&L.")

        object.__setattr__(self, "total_gross_pnl", float(self.total_gross_pnl))


class UnrealizedPnLAggregator:
    """Value zero or one explicitly supplied managed position."""

    def aggregate(self, position, reference_price=None):
        if position is None:
            if reference_price is not None:
                raise ValueError("Flat position state cannot have a reference price.")
            return UnrealizedPnLResult(position_count=0, total_gross_pnl=0.0)
        if not isinstance(position, ManagedPosition):
            raise TypeError("Position must be a ManagedPosition or None.")

        pnl = PositionPnLCalculator().calculate_open(position, reference_price)
        return UnrealizedPnLResult(
            position_count=1,
            total_gross_pnl=pnl.gross_pnl,
        )
