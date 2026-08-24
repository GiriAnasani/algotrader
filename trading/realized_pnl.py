"""Deterministic aggregation of realized gross position P&L."""

from dataclasses import dataclass
from math import fsum, isfinite
from numbers import Real

from trading.close_position_lifecycle import ClosedPosition
from trading.position_pnl import PositionPnLCalculator


@dataclass(frozen=True)
class RealizedPnLResult:
    """Immutable realized gross P&L summary."""

    trade_count: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    total_gross_pnl: float

    def __post_init__(self):
        counts = (
            self.trade_count,
            self.winning_trades,
            self.losing_trades,
            self.breakeven_trades,
        )
        if any(
            isinstance(count, bool) or not isinstance(count, int) or count < 0
            for count in counts
        ):
            raise ValueError("Trade counts must be non-negative integers.")
        if (
            self.winning_trades + self.losing_trades + self.breakeven_trades
            != self.trade_count
        ):
            raise ValueError("Classified trade counts must equal trade count.")
        if (
            isinstance(self.total_gross_pnl, bool)
            or not isinstance(self.total_gross_pnl, Real)
            or not isfinite(self.total_gross_pnl)
        ):
            raise ValueError("Total gross P&L must be a finite number.")

        object.__setattr__(self, "total_gross_pnl", float(self.total_gross_pnl))


class RealizedPnLAggregator:
    """Aggregate immutable closed positions into realized gross P&L."""

    def aggregate(self, closed_positions):
        if not isinstance(closed_positions, (list, tuple)):
            raise TypeError("Closed positions must be a list or tuple.")
        positions = tuple(closed_positions)
        if not all(isinstance(position, ClosedPosition) for position in positions):
            raise TypeError("Closed positions must contain ClosedPosition values.")

        calculator = PositionPnLCalculator()
        gross_values = tuple(
            calculator.calculate_closed(position).gross_pnl
            for position in positions
        )
        return RealizedPnLResult(
            trade_count=len(gross_values),
            winning_trades=sum(value > 0 for value in gross_values),
            losing_trades=sum(value < 0 for value in gross_values),
            breakeven_trades=sum(value == 0 for value in gross_values),
            total_gross_pnl=fsum(gross_values),
        )
