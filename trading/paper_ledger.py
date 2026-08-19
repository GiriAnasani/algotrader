"""In-memory history of completed paper trades."""

from trading.paper_trade import PaperTrade


class PaperTradeLedger:
    """Stores completed paper trades in insertion order."""

    def __init__(self):
        self._trades = []

    @property
    def count(self):
        return len(self._trades)

    def record(self, trade):
        """Appends one completed paper trade."""
        if not isinstance(trade, PaperTrade):
            raise TypeError("Trade must be a PaperTrade.")

        self._trades.append(trade)
        return trade

    def get_trades(self):
        """Returns an immutable snapshot of completed trades."""
        return tuple(self._trades)

    def get_latest_trade(self):
        """Returns the most recently completed trade, if any."""
        return self._trades[-1] if self._trades else None

    def clear(self):
        """Clears all completed trade history."""
        self._trades.clear()
