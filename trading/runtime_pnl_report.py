"""Pure reporting and serialization of trusted runtime P&L snapshots."""

from dataclasses import dataclass
from datetime import date, datetime
import json
from math import fsum, isfinite
from numbers import Real

from trading.runtime_pnl import RuntimePnLSnapshot


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
class RuntimePnLReport:
    """Immutable transport representation of a runtime P&L snapshot."""

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

    def to_dict(self):
        """Return a fresh plain-data representation with an ISO trading date."""
        return {
            "trading_date": self.trading_date.isoformat(),
            "closed_trade_count": self.closed_trade_count,
            "open_position_count": self.open_position_count,
            "realized_gross_pnl": self.realized_gross_pnl,
            "realized_total_charges": self.realized_total_charges,
            "realized_net_pnl": self.realized_net_pnl,
            "unrealized_gross_pnl": self.unrealized_gross_pnl,
            "portfolio_net_pnl": self.portfolio_net_pnl,
            "session_net_pnl": self.session_net_pnl,
        }

    def to_json(self):
        """Return deterministic compact JSON containing only report fields."""
        return json.dumps(self.to_dict(), allow_nan=False, separators=(",", ":"))


class RuntimePnLReporter:
    """Copy a trusted runtime snapshot into its reporting representation."""

    def build(self, snapshot):
        if not isinstance(snapshot, RuntimePnLSnapshot):
            raise TypeError("Snapshot must be a RuntimePnLSnapshot.")
        return RuntimePnLReport(
            trading_date=snapshot.trading_date,
            closed_trade_count=snapshot.closed_trade_count,
            open_position_count=snapshot.open_position_count,
            realized_gross_pnl=snapshot.realized_gross_pnl,
            realized_total_charges=snapshot.realized_total_charges,
            realized_net_pnl=snapshot.realized_net_pnl,
            unrealized_gross_pnl=snapshot.unrealized_gross_pnl,
            portfolio_net_pnl=snapshot.portfolio_net_pnl,
            session_net_pnl=snapshot.session_net_pnl,
        )
