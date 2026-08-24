"""Read-only composition of configured portfolio and session P&L snapshots."""

from dataclasses import dataclass
from datetime import date, datetime
from math import fsum, isfinite
from numbers import Real

from trading.ohlc import EXCHANGE_TIMEZONE
from trading.portfolio_net_pnl import PortfolioNetPnLAggregator
from trading.position import ManagedPosition
from trading.session_net_pnl import SessionNetPnLAggregator


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
class RuntimePnLSnapshot:
    """Immutable read-only view of current portfolio and session P&L."""

    trading_date: date
    closed_trade_count: int
    open_position_count: int
    realized_gross_pnl: float
    realized_total_charges: float
    realized_net_pnl: float
    unrealized_gross_pnl: float
    portfolio_net_pnl: float
    session_net_pnl: float

    def __post_init__(self):
        if isinstance(self.trading_date, datetime) or not isinstance(
            self.trading_date, date
        ):
            raise TypeError("Trading date must be a date, not a datetime.")
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
        session_net = _finite(self.session_net_pnl, "Session net P&L")
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
        object.__setattr__(self, "session_net_pnl", session_net)


class RuntimePnLSnapshotBuilder:
    """Build a snapshot solely from supplied runtime truth and dependencies."""

    def __init__(self, portfolio_net_pnl_aggregator, session_net_pnl_aggregator):
        if not isinstance(portfolio_net_pnl_aggregator, PortfolioNetPnLAggregator):
            raise TypeError(
                "Portfolio net P&L aggregator must be a PortfolioNetPnLAggregator."
            )
        if not isinstance(session_net_pnl_aggregator, SessionNetPnLAggregator):
            raise TypeError(
                "Session net P&L aggregator must be a SessionNetPnLAggregator."
            )
        self._portfolio_net_pnl_aggregator = portfolio_net_pnl_aggregator
        self._session_net_pnl_aggregator = session_net_pnl_aggregator

    def build(
        self,
        trading_date,
        closed_positions,
        active_position=None,
        reference_price=None,
        buy_order_count=1,
        sell_order_count=1,
    ):
        portfolio = self._portfolio_net_pnl_aggregator.aggregate(
            closed_positions,
            active_position,
            reference_price,
            buy_order_count,
            sell_order_count,
        )

        session_position = active_position
        session_reference_price = reference_price
        if (
            isinstance(trading_date, date)
            and not isinstance(trading_date, datetime)
            and isinstance(active_position, ManagedPosition)
            and active_position.entry_time.astimezone(EXCHANGE_TIMEZONE).date()
            != trading_date
        ):
            session_position = None
            session_reference_price = None
        session = self._session_net_pnl_aggregator.aggregate(
            trading_date,
            closed_positions,
            session_position,
            session_reference_price,
            buy_order_count,
            sell_order_count,
        )
        return RuntimePnLSnapshot(
            trading_date=trading_date,
            closed_trade_count=portfolio.closed_trade_count,
            open_position_count=portfolio.open_position_count,
            realized_gross_pnl=portfolio.realized_gross_pnl,
            realized_total_charges=portfolio.realized_total_charges,
            realized_net_pnl=portfolio.realized_net_pnl,
            unrealized_gross_pnl=portfolio.unrealized_gross_pnl,
            portfolio_net_pnl=portfolio.portfolio_net_pnl,
            session_net_pnl=session.session_net_pnl,
        )
