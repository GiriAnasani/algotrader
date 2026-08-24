"""Deterministic aggregation of realized net P&L."""

from dataclasses import dataclass
from math import fsum, isfinite
from numbers import Real

from trading.close_position_lifecycle import ClosedPosition
from trading.option_charges import NetPnLCalculator


def _validate_count(value, label, *, positive=False):
    minimum = 1 if positive else 0
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{label} must be a {qualifier} integer.")
    return value


def _validate_finite(value, label, *, non_negative=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not isfinite(value)
        or (non_negative and value < 0)
    ):
        qualifier = "finite non-negative" if non_negative else "finite"
        raise ValueError(f"{label} must be a {qualifier} number.")
    return float(value)


@dataclass(frozen=True)
class RealizedNetPnLResult:
    """Immutable summary of gross P&L, charges, and realized net P&L."""

    trade_count: int
    gross_pnl: float
    total_charges: float
    net_pnl: float
    net_winning_trades: int
    net_losing_trades: int
    net_breakeven_trades: int

    def __post_init__(self):
        trade_count = _validate_count(self.trade_count, "Trade count")
        winning = _validate_count(self.net_winning_trades, "Net winning trades")
        losing = _validate_count(self.net_losing_trades, "Net losing trades")
        breakeven = _validate_count(
            self.net_breakeven_trades, "Net breakeven trades"
        )
        if winning + losing + breakeven != trade_count:
            raise ValueError("Classified trade counts must equal trade count.")

        gross_pnl = _validate_finite(self.gross_pnl, "Gross P&L")
        total_charges = _validate_finite(
            self.total_charges, "Total charges", non_negative=True
        )
        net_pnl = _validate_finite(self.net_pnl, "Net P&L")
        if net_pnl != fsum((gross_pnl, -total_charges)):
            raise ValueError("Net P&L must equal gross P&L minus total charges.")

        object.__setattr__(self, "gross_pnl", gross_pnl)
        object.__setattr__(self, "total_charges", total_charges)
        object.__setattr__(self, "net_pnl", net_pnl)


class RealizedNetPnLAggregator:
    """Aggregate closed trades through an externally configured net calculator."""

    def __init__(self, net_pnl_calculator):
        if not isinstance(net_pnl_calculator, NetPnLCalculator):
            raise TypeError("Net P&L calculator must be a NetPnLCalculator.")
        self._net_pnl_calculator = net_pnl_calculator

    def aggregate(self, closed_positions, buy_order_count=1, sell_order_count=1):
        buy_order_count = _validate_count(
            buy_order_count, "Buy order count", positive=True
        )
        sell_order_count = _validate_count(
            sell_order_count, "Sell order count", positive=True
        )
        if not isinstance(closed_positions, (list, tuple)):
            raise TypeError("Closed positions must be a list or tuple.")
        positions = tuple(closed_positions)
        if not all(isinstance(position, ClosedPosition) for position in positions):
            raise TypeError("Closed positions must contain ClosedPosition values.")

        results = tuple(
            self._net_pnl_calculator.calculate(
                position, buy_order_count, sell_order_count
            )
            for position in positions
        )
        gross_pnl = fsum(result.gross_pnl for result in results)
        total_charges = fsum(result.total_charges for result in results)
        net_pnl = fsum((gross_pnl, -total_charges))
        return RealizedNetPnLResult(
            trade_count=len(results),
            gross_pnl=gross_pnl,
            total_charges=total_charges,
            net_pnl=net_pnl,
            net_winning_trades=sum(result.net_pnl > 0 for result in results),
            net_losing_trades=sum(result.net_pnl < 0 for result in results),
            net_breakeven_trades=sum(result.net_pnl == 0 for result in results),
        )
