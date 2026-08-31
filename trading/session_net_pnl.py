"""Deterministic net P&L snapshots for an explicit exchange trading date."""

from dataclasses import dataclass
from datetime import date, datetime
from math import fsum, isfinite
from numbers import Real

from trading.close_position_lifecycle import ClosedPosition
from trading.ohlc import EXCHANGE_TIMEZONE
from trading.position import ManagedPosition, PositionState
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
class SessionNetPnLResult:
    """Immutable realized-net plus unrealized-gross daily snapshot."""

    trading_date: date
    closed_trade_count: int
    open_position_count: int
    realized_gross_pnl: float
    realized_total_charges: float
    realized_net_pnl: float
    unrealized_gross_pnl: float
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
        session_net = _finite(self.session_net_pnl, "Session net P&L")
        if realized_net != fsum((realized_gross, -charges)):
            raise ValueError("Realized net P&L must equal gross P&L minus charges.")
        if session_net != fsum((realized_net, unrealized_gross)):
            raise ValueError(
                "Session net P&L must equal realized net plus unrealized gross."
            )

        object.__setattr__(self, "realized_gross_pnl", realized_gross)
        object.__setattr__(self, "realized_total_charges", charges)
        object.__setattr__(self, "realized_net_pnl", realized_net)
        object.__setattr__(self, "unrealized_gross_pnl", unrealized_gross)
        object.__setattr__(self, "session_net_pnl", session_net)


class SessionNetPnLAggregator:
    """Calculate net P&L for one caller-supplied exchange date."""

    def __init__(self, realized_net_pnl_aggregator):
        if not isinstance(realized_net_pnl_aggregator, RealizedNetPnLAggregator):
            raise TypeError(
                "Realized net P&L aggregator must be a RealizedNetPnLAggregator."
            )
        self._realized_net_pnl_aggregator = realized_net_pnl_aggregator

    @property
    def realized_net_pnl_aggregator(self):
        return self._realized_net_pnl_aggregator

    def aggregate(
        self,
        trading_date,
        closed_positions,
        active_position=None,
        reference_price=None,
        buy_order_count=1,
        sell_order_count=1,
    ):
        if isinstance(trading_date, datetime) or not isinstance(trading_date, date):
            raise TypeError("Trading date must be a date, not a datetime.")
        if not isinstance(closed_positions, (list, tuple)):
            raise TypeError("Closed positions must be a list or tuple.")
        positions = tuple(closed_positions)
        if not all(isinstance(position, ClosedPosition) for position in positions):
            raise TypeError("Closed positions must contain ClosedPosition values.")
        if active_position is not None and not isinstance(
            active_position, ManagedPosition
        ):
            raise TypeError("Active position must be a ManagedPosition or None.")
        if active_position is not None and active_position.state is not PositionState.OPEN:
            raise ValueError("Active position must be OPEN.")

        matching_closed = tuple(
            position
            for position in positions
            if position.exit_time.astimezone(EXCHANGE_TIMEZONE).date() == trading_date
        )
        realized = self._realized_net_pnl_aggregator.aggregate(
            matching_closed, buy_order_count, sell_order_count
        )

        active_matches = (
            active_position is not None
            and active_position.entry_time.astimezone(EXCHANGE_TIMEZONE).date()
            == trading_date
        )
        if not active_matches and reference_price is not None:
            raise ValueError(
                "A reference price requires an active position in the session."
            )
        session_position = active_position if active_matches else None
        unrealized = UnrealizedPnLAggregator().aggregate(
            session_position, reference_price
        )
        return SessionNetPnLResult(
            trading_date=trading_date,
            closed_trade_count=realized.trade_count,
            open_position_count=unrealized.position_count,
            realized_gross_pnl=realized.gross_pnl,
            realized_total_charges=realized.total_charges,
            realized_net_pnl=realized.net_pnl,
            unrealized_gross_pnl=unrealized.total_gross_pnl,
            session_net_pnl=fsum(
                (realized.net_pnl, unrealized.total_gross_pnl)
            ),
        )
