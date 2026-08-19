"""Pure realized P&L calculations for completed paper trades."""

from dataclasses import dataclass

from trading.paper_trade import PaperTrade


@dataclass(frozen=True)
class PaperTradePnL:
    """Realized points and gross P&L for one completed paper trade."""

    points_pnl: float
    gross_pnl: float


def calculate_trade_pnl(trade):
    """Calculates realized P&L for one long-option PaperTrade."""
    if not isinstance(trade, PaperTrade):
        raise TypeError("Trade must be a PaperTrade.")

    points_pnl = float(trade.exit_price - trade.entry_price)
    gross_pnl = float(points_pnl * trade.quantity)

    return PaperTradePnL(
        points_pnl=points_pnl,
        gross_pnl=gross_pnl,
    )
