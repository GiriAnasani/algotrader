import pandas as pd
from pathlib import Path


class InstrumentManager:
    """
    Handles downloading, loading and searching
    Zerodha instruments.
    """

    def __init__(self, kite):

        self.kite = kite

        self.instrument_file = Path(
            "data/instruments/instruments.csv"
        )

        self.df = None

    def download_instruments(self):
        """
        Download all instruments from Zerodha
        and save them locally.
        """

        instruments = self.kite.instruments()

        df = pd.DataFrame(instruments)

        self.instrument_file.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        df.to_csv(
            self.instrument_file,
            index=False
        )

        self.df = df

        return len(df)

    def load_instruments(self):
        """
        Load instruments from CSV.

        Uses in-memory cache after first load.
        """

        if self.df is not None:
            return self.df

        if not self.instrument_file.exists():
            raise FileNotFoundError(
                "Instrument file not found. "
                "Please run download_instruments() first."
            )

        self.df = pd.read_csv(self.instrument_file)

        return self.df

    def search(self, symbol):
        """
        Search instruments using partial matching.
        """

        df = self.load_instruments()

        return self._format_output(
            df[
                df["tradingsymbol"].str.contains(
                    symbol,
                    case=False,
                    na=False
                )
            ]
        )

    def _find_instruments(self, **filters):
        """
        Internal helper to filter instruments
        using one or more column/value pairs.

        Supports:
        - Exact matching
        - Partial matching using *_contains
        """

        df = self.load_instruments()

        results = df

        for column, value in filters.items():

            if value is None:
                continue

            # Partial matching
            if column.endswith("_contains"):

                actual_column = column.replace(
                    "_contains",
                    ""
                )

                results = results[
                    results[actual_column].str.contains(
                        value,
                        case=False,
                        na=False
                    )
                ]

            # Exact match for trading symbol
            elif column == "tradingsymbol":

                results = results[
                    results[column].str.upper()
                    == value.upper()
                ]

            # Exact match for everything else
            else:

                results = results[
                    results[column] == value
                ]

        return results

    def _format_output(self, df):
        """
        Returns only the useful columns
        for displaying instrument data.
        """

        columns = [
            "tradingsymbol",
            "exchange",
            "instrument_type",
            "expiry",
            "strike",
            "lot_size",
            "instrument_token"
        ]

        available_columns = [
            column
            for column in columns
            if column in df.columns
        ]

        return df[available_columns]

    def get_equity(self, symbol):
        """
        Returns the NSE equity instrument.
        """

        symbol = symbol.strip().upper()

        if not symbol:
            raise ValueError(
                "Trading symbol cannot be empty."
            )

        results = self._find_instruments(
            tradingsymbol=symbol,
            exchange="NSE",
            instrument_type="EQ"
        )

        return self._format_output(results)

    def get_futures(self, symbol):
        """
        Returns all futures contracts
        for the given trading symbol.
        """

        symbol = symbol.strip().upper()

        if not symbol:
            raise ValueError(
                "Trading symbol cannot be empty."
            )

        results = self._find_instruments(
            tradingsymbol_contains=symbol,
            exchange="NFO",
            instrument_type="FUT"
        )

        return self._format_output(results)

    def get_options(self, symbol, option_type=None):
        """
        Returns option contracts
        for the given symbol.

        option_type:
            None -> All Options
            CE   -> Call Options
            PE   -> Put Options
        """

        symbol = symbol.strip().upper()

        if not symbol:
            raise ValueError(
                "Trading symbol cannot be empty."
            )

        if option_type is not None:

            option_type = option_type.upper()

            if option_type not in ("CE", "PE"):
                raise ValueError(
                    "Option type must be CE or PE."
                )

        results = self._find_instruments(
            tradingsymbol_contains=symbol,
            exchange="NFO"
        )

        results = results[
            results["instrument_type"].isin(
                ["CE", "PE"]
            )
        ]

        if option_type:

            results = results[
                results["instrument_type"]
                == option_type
            ]

        return self._format_output(results)

    def get_by_token(self, token):
        """
        Returns the instrument
        for the given instrument token.
        """

        if token is None:
            raise ValueError(
                "Instrument token cannot be empty."
            )

        results = self._find_instruments(
            instrument_token=token
        )

        return self._format_output(results)

    def get_instrument_token(self, symbol):
        """
        Returns the instrument token
        for the given NSE equity symbol.
        """

        instrument = self.get_equity(symbol)

        if instrument.empty:
            raise ValueError(
                f"Instrument not found: {symbol}"
            )

        return int(
            instrument.iloc[0]["instrument_token"]
        )