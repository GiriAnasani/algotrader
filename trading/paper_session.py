"""Derived summaries of completed paper-trading sessions."""

from dataclasses import dataclass

from trading.paper_pnl import calculate_trade_pnl


@dataclass(frozen=True)
class PaperSessionSummary:
    """Aggregate realized results derived from completed paper trades."""

    completed_trades: int
    winning_trades: int
    losing_trades: int
    flat_trades: int
    total_points_pnl: float
    total_gross_pnl: float


def calculate_session_summary(trades):
    """Calculates a deterministic realized summary from completed trades."""
    completed_trades = 0
    winning_trades = 0
    losing_trades = 0
    flat_trades = 0
    total_points_pnl = 0.0
    total_gross_pnl = 0.0

    for trade in trades:
        pnl = calculate_trade_pnl(trade)
        completed_trades += 1
        total_points_pnl += pnl.points_pnl
        total_gross_pnl += pnl.gross_pnl

        if pnl.gross_pnl > 0:
            winning_trades += 1
        elif pnl.gross_pnl < 0:
            losing_trades += 1
        else:
            flat_trades += 1

    return PaperSessionSummary(
        completed_trades=completed_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        flat_trades=flat_trades,
        total_points_pnl=float(total_points_pnl),
        total_gross_pnl=float(total_gross_pnl),
    )
