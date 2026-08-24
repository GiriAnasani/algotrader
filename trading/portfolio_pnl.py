"""Deterministic composition of realized and unrealized gross P&L."""

from dataclasses import dataclass
from math import fsum, isfinite
from numbers import Real

from trading.realized_pnl import RealizedPnLAggregator
from trading.unrealized_pnl import UnrealizedPnLAggregator


@dataclass(frozen=True)
class PortfolioPnLResult:
    """Immutable combined gross P&L snapshot."""

    closed_trade_count: int
    open_position_count: int
    realized_gross_pnl: float
    unrealized_gross_pnl: float
    total_gross_pnl: float

    def __post_init__(self):
        if (
            isinstance(self.closed_trade_count, bool)
            or not isinstance(self.closed_trade_count, int)
            or self.closed_trade_count < 0
        ):
            raise ValueError("Closed trade count must be a non-negative integer.")
        if (
            isinstance(self.open_position_count, bool)
            or not isinstance(self.open_position_count, int)
            or self.open_position_count not in (0, 1)
        ):
            raise ValueError("Open position count must be zero or one.")

        values = (
            self.realized_gross_pnl,
            self.unrealized_gross_pnl,
            self.total_gross_pnl,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not isfinite(value)
            for value in values
        ):
            raise ValueError("Gross P&L values must be finite numbers.")

        expected_total = fsum(
            (self.realized_gross_pnl, self.unrealized_gross_pnl)
        )
        if self.total_gross_pnl != expected_total:
            raise ValueError("Total gross P&L must equal its component totals.")

        object.__setattr__(
            self, "realized_gross_pnl", float(self.realized_gross_pnl)
        )
        object.__setattr__(
            self, "unrealized_gross_pnl", float(self.unrealized_gross_pnl)
        )
        object.__setattr__(self, "total_gross_pnl", float(self.total_gross_pnl))


class PortfolioPnLAggregator:
    """Combine existing realized and unrealized gross P&L summaries."""

    def aggregate(
        self,
        closed_positions,
        active_position=None,
        reference_price=None,
    ):
        realized = RealizedPnLAggregator().aggregate(closed_positions)
        unrealized = UnrealizedPnLAggregator().aggregate(
            active_position, reference_price
        )
        return PortfolioPnLResult(
            closed_trade_count=realized.trade_count,
            open_position_count=unrealized.position_count,
            realized_gross_pnl=realized.total_gross_pnl,
            unrealized_gross_pnl=unrealized.total_gross_pnl,
            total_gross_pnl=fsum(
                (realized.total_gross_pnl, unrealized.total_gross_pnl)
            ),
        )
