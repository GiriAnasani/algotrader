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



def _strategy2_instruments():
    instruments = InstrumentManager(kite=None)
    instruments.df = pd.DataFrame(
        [
            {
                "name": "NIFTY",
                "exchange": "NFO",
                "segment": "NFO-OPT",
                "instrument_type": "CE",
                "expiry": "2026-09-29",
                "strike": 25000.0,
                "instrument_token": 200,
                "tradingsymbol": "NIFTY26SEP25000CE",
                "lot_size": 65,
            },
            {
                "name": "NIFTY",
                "exchange": "NFO",
                "segment": "NFO-OPT",
                "instrument_type": "PE",
                "expiry": "2026-09-29",
                "strike": 25000.0,
                "instrument_token": 201,
                "tradingsymbol": "NIFTY26SEP25000PE",
                "lot_size": 65,
            },
            {
                "name": "NIFTY",
                "exchange": "NFO",
                "segment": "NFO-OPT",
                "instrument_type": "CE",
                "expiry": "2026-09-29",
                "strike": 25050.0,
                "instrument_token": 202,
                "tradingsymbol": "NIFTY26SEP25050CE",
                "lot_size": 65,
            },
            {
                "name": "NIFTY",
                "exchange": "NFO",
                "segment": "NFO-OPT",
                "instrument_type": "PE",
                "expiry": "2026-09-29",
                "strike": 24950.0,
                "instrument_token": 203,
                "tradingsymbol": "NIFTY26SEP24950PE",
                "lot_size": 65,
            },
            {
                "name": "NIFTY",
                "exchange": "NFO",
                "segment": "NFO-OPT",
                "instrument_type": "CE",
                "expiry": "2026-10-06",
                "strike": 25050.0,
                "instrument_token": 204,
                "tradingsymbol": "NIFTY26OCT25050CE",
                "lot_size": 65,
            },
            {
                "name": "NIFTY",
                "exchange": "NFO",
                "segment": "NFO-OPT",
                "instrument_type": "PE",
                "expiry": "2026-10-06",
                "strike": 24950.0,
                "instrument_token": 205,
                "tradingsymbol": "NIFTY26OCT24950PE",
                "lot_size": 65,
            },
        ]
    )
    return instruments


def test_strategy2_selects_one_step_otm_ce():
    instruments = _strategy2_instruments()

    contract = instruments.get_nifty_strategy2_contract(
        25024.0,
        "CE",
        as_of=date(2026, 9, 29),
    )

    assert contract["atm_strike"] == 25000.0
    assert contract["otm_strike"] == 25050.0
    assert contract["strike"] == 25050.0
    assert contract["instrument_token"] == 202
    assert contract["option_type"] == "CE"


def test_strategy2_selects_one_step_otm_pe():
    instruments = _strategy2_instruments()

    contract = instruments.get_nifty_strategy2_contract(
        25024.0,
        "PE",
        as_of=date(2026, 9, 29),
    )

    assert contract["atm_strike"] == 25000.0
    assert contract["otm_strike"] == 24950.0
    assert contract["strike"] == 24950.0
    assert contract["instrument_token"] == 203
    assert contract["option_type"] == "PE"
