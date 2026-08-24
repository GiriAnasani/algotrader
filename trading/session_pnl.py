"""Deterministic exchange-date gross P&L snapshots."""

from dataclasses import dataclass
from datetime import date, datetime
from math import fsum, isfinite
from numbers import Real

from trading.close_position_lifecycle import ClosedPosition
from trading.ohlc import EXCHANGE_TIMEZONE
from trading.position import ManagedPosition, PositionState
from trading.realized_pnl import RealizedPnLAggregator
from trading.unrealized_pnl import UnrealizedPnLAggregator


@dataclass(frozen=True)
class SessionPnLResult:
    """Immutable gross P&L snapshot for one explicit exchange date."""

    trading_date: date
    closed_trade_count: int
    open_position_count: int
    realized_gross_pnl: float
    unrealized_gross_pnl: float
    total_gross_pnl: float

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


class SessionPnLAggregator:
    """Calculate gross P&L for one caller-supplied exchange date."""

    def aggregate(
        self,
        trading_date,
        closed_positions,
        active_position=None,
        reference_price=None,
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
        if (
            active_position is not None
            and active_position.state is not PositionState.OPEN
        ):
            raise ValueError("Active position must be OPEN.")

        matching_closed = tuple(
            position
            for position in positions
            if position.exit_time.astimezone(EXCHANGE_TIMEZONE).date()
            == trading_date
        )
        realized = RealizedPnLAggregator().aggregate(matching_closed)

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
        return SessionPnLResult(
            trading_date=trading_date,
            closed_trade_count=realized.trade_count,
            open_position_count=unrealized.position_count,
            realized_gross_pnl=realized.total_gross_pnl,
            unrealized_gross_pnl=unrealized.total_gross_pnl,
            total_gross_pnl=fsum(
                (realized.total_gross_pnl, unrealized.total_gross_pnl)
            ),
        )
