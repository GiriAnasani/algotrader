"""Deterministic portfolio snapshot of realized net and unrealized gross P&L."""

from dataclasses import dataclass
from math import fsum, isfinite
from numbers import Real

from trading.realized_net_pnl import RealizedNetPnLAggregator
from trading.unrealized_pnl import UnrealizedPnLAggregator


def _finite(value, label, *, non_negative=False):
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
class PortfolioNetPnLResult:
    """Immutable realized-net plus unrealized-gross portfolio snapshot."""

    closed_trade_count: int
    open_position_count: int
    realized_gross_pnl: float
    realized_total_charges: float
    realized_net_pnl: float
    unrealized_gross_pnl: float
    portfolio_net_pnl: float

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

        realized_gross = _finite(self.realized_gross_pnl, "Realized gross P&L")
        charges = _finite(
            self.realized_total_charges,
            "Realized total charges",
            non_negative=True,
        )
        realized_net = _finite(self.realized_net_pnl, "Realized net P&L")
        unrealized_gross = _finite(
            self.unrealized_gross_pnl, "Unrealized gross P&L"
        )
        portfolio_net = _finite(self.portfolio_net_pnl, "Portfolio net P&L")
        if realized_net != fsum((realized_gross, -charges)):
            raise ValueError("Realized net P&L must equal gross P&L minus charges.")
        if portfolio_net != fsum((realized_net, unrealized_gross)):
            raise ValueError(
                "Portfolio net P&L must equal realized net plus unrealized gross."
            )

        object.__setattr__(self, "realized_gross_pnl", realized_gross)
        object.__setattr__(self, "realized_total_charges", charges)
        object.__setattr__(self, "realized_net_pnl", realized_net)
        object.__setattr__(self, "unrealized_gross_pnl", unrealized_gross)
        object.__setattr__(self, "portfolio_net_pnl", portfolio_net)


class PortfolioNetPnLAggregator:
    """Compose realized net P&L with optional open-position gross P&L."""

    def __init__(self, realized_net_pnl_aggregator):
        if not isinstance(realized_net_pnl_aggregator, RealizedNetPnLAggregator):
            raise TypeError(
                "Realized net P&L aggregator must be a RealizedNetPnLAggregator."
            )
        self._realized_net_pnl_aggregator = realized_net_pnl_aggregator

    def aggregate(
        self,
        closed_positions,
        active_position=None,
        reference_price=None,
        buy_order_count=1,
        sell_order_count=1,
    ):
        realized = self._realized_net_pnl_aggregator.aggregate(
            closed_positions, buy_order_count, sell_order_count
        )
        unrealized = UnrealizedPnLAggregator().aggregate(
            active_position, reference_price
        )
        return PortfolioNetPnLResult(
            closed_trade_count=realized.trade_count,
            open_position_count=unrealized.position_count,
            realized_gross_pnl=realized.gross_pnl,
            realized_total_charges=realized.total_charges,
            realized_net_pnl=realized.net_pnl,
            unrealized_gross_pnl=unrealized.total_gross_pnl,
            portfolio_net_pnl=fsum(
                (realized.net_pnl, unrealized.total_gross_pnl)
            ),
        )
