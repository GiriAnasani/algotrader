"""Dedicated zero-order live-data runner for frozen Strategy 2."""

from datetime import date, datetime
import csv
from io import StringIO
import json
import math
import os
from pathlib import Path
import signal
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.auth import ZerodhaAuth
from trading.instruments import InstrumentManager
from trading.market import MarketData
from trading.ohlc import EXCHANGE_TIMEZONE
from trading.strategy2_forward_telemetry import (
    STRATEGY_ID,
    Strategy2ForwardTelemetryStore,
)


OBSERVATION_FILENAME = f"{STRATEGY_ID}_live_observations.jsonl"
UNFINISHED_STATUSES = {"ENTRY_PENDING", "SHADOW_POSITION_ACTIVE"}
_SECRET_FIELDS = {"access_token", "api_secret", "request_token"}


class ShadowSafetyError(RuntimeError):
    """Raised when an order capability is requested in shadow mode."""


class UnfinishedForwardLifecycleError(RuntimeError):
    """Raised when authoritative telemetry contains an unfinished lifecycle."""


class RestrictedKiteClient:
    """Read/market-data-only view of an authenticated Kite client."""

    _ROOT = "https://api.kite.trade"

    def __init__(self, api_key, access_token):
        self._api_key = str(api_key)
        self._access_token = str(access_token)

    @property
    def access_token(self):
        return self._access_token

    def historical_data(
        self, instrument_token, from_date, to_date, interval,
        continuous=False, oi=False,
    ):
        date_format = "%Y-%m-%d %H:%M:%S"
        parameters = urlencode({
            "from": (
                from_date.strftime(date_format)
                if isinstance(from_date, datetime) else from_date
            ),
            "to": (
                to_date.strftime(date_format)
                if isinstance(to_date, datetime) else to_date
            ),
            "interval": interval,
            "continuous": 1 if continuous else 0,
            "oi": 1 if oi else 0,
        })
        path = f"/instruments/historical/{int(instrument_token)}/{interval}"
        request = Request(
            f"{self._ROOT}{path}?{parameters}",
            headers=self._headers(),
            method="GET",
        )
        with urlopen(request, timeout=7) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("status") == "error" or payload.get("error_type"):
            raise RuntimeError("Kite historical-data request failed.")
        records = []
        for candle in payload["data"]["candles"]:
            record = {
                "date": datetime.fromisoformat(candle[0]),
                "open": candle[1],
                "high": candle[2],
                "low": candle[3],
                "close": candle[4],
                "volume": candle[5],
            }
            if len(candle) == 7:
                record["oi"] = candle[6]
            records.append(record)
        return records

    def instruments(self, exchange=None):
        path = "/instruments"
        if exchange:
            path += f"/{str(exchange).strip().upper()}"
        request = Request(
            f"{self._ROOT}{path}",
            headers=self._headers(),
            method="GET",
        )
        with urlopen(request, timeout=7) as response:
            document = response.read().decode("utf-8").strip()
        records = []
        for row in csv.DictReader(StringIO(document)):
            row["instrument_token"] = int(row["instrument_token"])
            row["last_price"] = float(row["last_price"])
            row["strike"] = float(row["strike"])
            row["tick_size"] = float(row["tick_size"])
            row["lot_size"] = int(row["lot_size"])
            if len(row["expiry"]) == 10:
                row["expiry"] = date.fromisoformat(row["expiry"])
            records.append(row)
        return records

    def _headers(self):
        return {
            "X-Kite-Version": "3",
            "Authorization": f"token {self._api_key}:{self._access_token}",
            "User-Agent": "strategy2-live-shadow/1",
        }

    def place_order(self, *args, **kwargs):
        raise ShadowSafetyError("Broker orders are disabled in Strategy 2 shadow mode.")

    def modify_order(self, *args, **kwargs):
        raise ShadowSafetyError("Broker orders are disabled in Strategy 2 shadow mode.")

    def cancel_order(self, *args, **kwargs):
        raise ShadowSafetyError("Broker orders are disabled in Strategy 2 shadow mode.")


class Strategy2LiveObservationLog:
    """Append-only diagnostic JSONL; never authoritative lifecycle state."""

    def __init__(self, root=None, clock=None):
        if root is None:
            root = Path(os.getenv("JARVIS_STATE_DIR", ".")) / "strategy2_forward"
        self.root = Path(root)
        self.path = self.root / OBSERVATION_FILENAME
        self._clock = clock or (lambda: datetime.now(EXCHANGE_TIMEZONE))
        self.root.mkdir(parents=True, exist_ok=True)

    def record(self, event_type, **fields):
        if any(key.lower() in _SECRET_FIELDS for key in fields):
            raise ShadowSafetyError("Secrets cannot be written to diagnostics.")
        document = {
            "event_type": str(event_type),
            "event_time": self._clock(),
            **fields,
        }
        encoded = json.dumps(
            document,
            default=_json_value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        with self.path.open("a", encoding="utf-8", newline="\n") as target:
            target.write(encoded + "\n")
            target.flush()
            os.fsync(target.fileno())


def assert_clean_forward_lifecycle(telemetry_store):
    unfinished = [
        record for record in telemetry_store.records
        if record["entry_status"] in UNFINISHED_STATUSES
    ]
    if unfinished:
        raise UnfinishedForwardLifecycleError(
            "STRATEGY2_SHADOW_START_BLOCKED: UNFINISHED_FORWARD_LIFECYCLE"
        )


def build_shadow_market(
    authenticated_kite,
    *,
    telemetry_store=None,
    observation_log=None,
    ticker_factory=None,
    clock=None,
):
    telemetry_store = telemetry_store or Strategy2ForwardTelemetryStore()
    assert_clean_forward_lifecycle(telemetry_store)
    restricted_kite = RestrictedKiteClient(
        authenticated_kite.api_key,
        authenticated_kite.access_token,
    )
    instruments = InstrumentManager(restricted_kite)
    return MarketData.create_strategy2_live_shadow(
        restricted_kite,
        instruments,
        strategy2_telemetry_store=telemetry_store,
        observation_log=observation_log,
        ticker_factory=ticker_factory,
        clock=clock,
    )


def run():
    telemetry_store = Strategy2ForwardTelemetryStore()
    assert_clean_forward_lifecycle(telemetry_store)
    observations = Strategy2LiveObservationLog()
    observations.record(
        "RUNNER_START",
        strategy_state="IDLE",
        data_quality_flags=[],
    )
    print("STRATEGY2_LIVE_SHADOW=ENABLED")
    print("BROKER_ORDER_CAPABILITY=DISABLED")
    print("STRATEGY1=DISABLED")

    market = None
    try:
        authenticated_kite = ZerodhaAuth().get_authenticated_client()
        market = build_shadow_market(
            authenticated_kite,
            telemetry_store=telemetry_store,
            observation_log=observations,
        )
        del authenticated_kite
        market.warm_indicators_from_history("NIFTY 50")
        market.connect_strategy2_live_shadow("NIFTY 50")
    except KeyboardInterrupt:
        pass
    except Exception as error:
        observations.record(
            "ERROR",
            error_type=type(error).__name__,
        )
        raise
    finally:
        if market is not None:
            market.disconnect_strategy2_live_shadow()
        observations.record("RUNNER_STOP")


def _json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite diagnostics are not allowed.")
    raise TypeError(f"Unsupported diagnostic value: {type(value).__name__}")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.default_int_handler)
    run()
