"""Pure translation from live intents to Zerodha order requests."""

from trading.live_order import LiveOrderIntent
from trading.strategy import SignalAction
from trading.zerodha_order import ZerodhaOrderRequest


class ZerodhaOrderAdapter:
    """Builds Zerodha-compatible requests without submitting them."""

    def to_order_request(self, intent):
        """Translates one validated live order intent into an order request."""
        if not isinstance(intent, LiveOrderIntent):
            raise TypeError("Intent must be a LiveOrderIntent.")

        transaction_type = (
            "BUY"
            if intent.action in (SignalAction.BUY_CE, SignalAction.BUY_PE)
            else "SELL"
        )

        return ZerodhaOrderRequest(
            tradingsymbol=intent.contract_symbol,
            exchange="NFO",
            transaction_type=transaction_type,
            quantity=intent.quantity,
            order_type="MARKET",
            product="MIS",
            validity="DAY",
        )
