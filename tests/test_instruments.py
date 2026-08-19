from datetime import date

import pandas as pd

from trading.instruments import InstrumentManager


def test_nifty_option_pair_returns_each_contracts_csv_lot_size():
    instruments = InstrumentManager(kite=None)
    instruments.df = pd.DataFrame(
        [
            {
                "name": "NIFTY",
                "exchange": "NFO",
                "segment": "NFO-OPT",
                "instrument_type": "CE",
                "expiry": "2026-08-20",
                "strike": 25000.0,
                "instrument_token": 101,
                "tradingsymbol": "NIFTY2682025000CE",
                "lot_size": 75,
            },
            {
                "name": "NIFTY",
                "exchange": "NFO",
                "segment": "NFO-OPT",
                "instrument_type": "PE",
                "expiry": "2026-08-20",
                "strike": 25000.0,
                "instrument_token": 102,
                "tradingsymbol": "NIFTY2682025000PE",
                "lot_size": 50,
            },
            {
                "name": "NIFTY",
                "exchange": "NFO",
                "segment": "NFO-OPT",
                "instrument_type": "CE",
                "expiry": "2026-08-27",
                "strike": 25000.0,
                "instrument_token": 103,
                "tradingsymbol": "NIFTY2682725000CE",
                "lot_size": 25,
            },
            {
                "name": "NIFTY",
                "exchange": "NFO",
                "segment": "NFO-OPT",
                "instrument_type": "PE",
                "expiry": "2026-08-27",
                "strike": 25000.0,
                "instrument_token": 104,
                "tradingsymbol": "NIFTY2682725000PE",
                "lot_size": 25,
            },
        ]
    )

    pair = instruments.get_nifty_option_pair(
        25024.0,
        as_of=date(2026, 8, 19),
    )

    assert pair["CE"]["tradingsymbol"] == "NIFTY2682025000CE"
    assert pair["PE"]["tradingsymbol"] == "NIFTY2682025000PE"
    assert pair["CE"]["lot_size"] == 75
    assert pair["PE"]["lot_size"] == 50
    assert isinstance(pair["CE"]["lot_size"], int)
    assert isinstance(pair["PE"]["lot_size"], int)
