from core.auth import ZerodhaAuth
from trading.instruments import InstrumentManager
from trading.market import MarketData


def main():

    auth = ZerodhaAuth()

    kite = auth.get_authenticated_client()

    instruments = InstrumentManager(
        kite
    )

    market = MarketData(
        kite,
        instruments
    )

    market.warm_indicators_from_history(
        "NIFTY 50"
    )

    market.connect_live(
        "NIFTY 50"
    )


if __name__ == "__main__":
    main()
