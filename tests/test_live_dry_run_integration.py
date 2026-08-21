from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from trading.candle import Candle
from trading.execution_mode import ExecutionMode
from trading.live_execution import (
    LiveExecutionCoordinator,
    LiveExecutionDisabledError,
)
from trading.market import MarketData
from trading.market import PendingLiveOrderError
from trading.strategy import IndicatorSnapshot, SignalAction, StrategyResult
from trading.zerodha_order_adapter import ZerodhaOrderAdapter
from trading.zerodha_order_status_reader import ZerodhaOrderStatusReader
from trading.zerodha_order_submitter import ZerodhaOrderSubmitter


IST = ZoneInfo("Asia/Kolkata")
TIME = datetime(2026, 8, 21, 9, 15, tzinfo=IST)


class FakeKiteClient:
    def __init__(self, exception=None, status_exception=None, status_records=None):
        self.exception = exception
        self.status_exception = status_exception
        self.status_records = list(status_records or [])
        self.calls = []
        self.order_history_calls = []
        self.history_by_order_id = {}

    def place_order(self, **kwargs):
        self.calls.append(kwargs)
        if self.exception is not None:
            raise self.exception
        order_id = f"fake-order-{len(self.calls)}"
        record = (
            self.status_records.pop(0)
            if self.status_records
            else {
                "status": "COMPLETE",
                "filled_quantity": kwargs["quantity"],
                "pending_quantity": 0,
                "average_price": 27.0,
            }
        )
        record = {**record, "order_id": order_id}
        self.history_by_order_id[order_id] = [record]
        return order_id

    def order_history(self, order_id):
        self.order_history_calls.append(order_id)
        if self.status_exception is not None:
            raise self.status_exception
        return self.history_by_order_id[order_id]


def make_market(enabled=True, client=None, status_records=None, status_exception=None):
    client = client or FakeKiteClient(
        status_records=status_records,
        status_exception=status_exception,
    )
    coordinator = LiveExecutionCoordinator(
        ZerodhaOrderAdapter(),
        ZerodhaOrderSubmitter(client),
        enabled=enabled,
    )
    status_reader = ZerodhaOrderStatusReader(client)
    market = MarketData(
        kite=None,
        instruments=None,
        execution_mode=ExecutionMode.LIVE,
        live_execution_coordinator=coordinator,
        live_order_status_reader=status_reader,
    )
    market.nifty_option_pair = {
        "CE": {"tradingsymbol": "NIFTY2682125000CE", "lot_size": 75},
        "PE": {"tradingsymbol": "NIFTY2682125000PE", "lot_size": 50},
    }
    market.latest_option_premiums = {"CE": 27.0, "PE": 31.0}
    return market, client


def result(action, actions=None):
    return StrategyResult(
        strategy_name="test",
        action=action,
        candle_time=TIME,
        actions=actions or (action,),
    )


def set_strategy_position(market, side, premium):
    market.strategy_engine.active_position = side
    market.strategy_engine.entry_premium = premium


def replace_status(client, order_id, record):
    client.history_by_order_id[order_id] = [{**record, "order_id": order_id}]


def test_market_data_defaults_to_paper_mode():
    market = MarketData(kite=None, instruments=None)

    assert market.execution_router.mode is ExecutionMode.PAPER
    assert market.strategy_engine.target_points == 2.0
    assert market.pending_live_order is None


def test_paper_execution_never_creates_pending_state_or_reads_live_status():
    client = FakeKiteClient()
    market = MarketData(
        kite=None,
        instruments=None,
        live_execution_coordinator=LiveExecutionCoordinator(
            ZerodhaOrderAdapter(),
            ZerodhaOrderSubmitter(client),
            enabled=True,
        ),
        live_order_status_reader=ZerodhaOrderStatusReader(client),
    )
    market.nifty_option_pair = {
        "CE": {"tradingsymbol": "NIFTY2682125000CE", "lot_size": 75},
        "PE": {"tradingsymbol": "NIFTY2682125000PE", "lot_size": 50},
    }
    market.latest_option_premiums = {"CE": 27.0, "PE": 31.0}
    set_strategy_position(market, "CE", 27.0)

    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)

    assert market.pending_live_order is None
    assert client.calls == []
    assert client.order_history_calls == []


@pytest.mark.parametrize("execution_mode", ["LIVE", None])
def test_invalid_market_execution_mode_is_rejected(execution_mode):
    with pytest.raises(TypeError, match="ExecutionMode"):
        MarketData(None, None, execution_mode=execution_mode)


def test_live_market_requires_an_explicit_coordinator():
    with pytest.raises(TypeError, match="LiveExecutionCoordinator"):
        MarketData(None, None, execution_mode=ExecutionMode.LIVE)


def test_live_market_requires_an_explicit_status_reader():
    coordinator = LiveExecutionCoordinator(
        ZerodhaOrderAdapter(),
        ZerodhaOrderSubmitter(FakeKiteClient()),
        enabled=True,
    )

    with pytest.raises(TypeError, match="ZerodhaOrderStatusReader"):
        MarketData(
            None,
            None,
            execution_mode=ExecutionMode.LIVE,
            live_execution_coordinator=coordinator,
        )


def test_live_market_rejects_invalid_status_reader():
    coordinator = LiveExecutionCoordinator(
        ZerodhaOrderAdapter(),
        ZerodhaOrderSubmitter(FakeKiteClient()),
        enabled=True,
    )

    with pytest.raises(TypeError, match="ZerodhaOrderStatusReader"):
        MarketData(
            None,
            None,
            execution_mode=ExecutionMode.LIVE,
            live_execution_coordinator=coordinator,
            live_order_status_reader=object(),
        )


@pytest.mark.parametrize(
    ("action", "side", "expected_quantity"),
    [
        (SignalAction.BUY_CE, "CE", 75),
        (SignalAction.BUY_PE, "PE", 50),
    ],
)
def test_live_buy_reaches_fake_broker_once(action, side, expected_quantity):
    market, client = make_market()
    set_strategy_position(market, side, market.latest_option_premiums[side])

    order_ids = market._execute_strategy_result(result(action), TIME)

    assert order_ids == ("fake-order-1",)
    assert len(client.calls) == 1
    assert client.calls[0]["tradingsymbol"] == f"NIFTY2682125000{side}"
    assert client.calls[0]["quantity"] == expected_quantity
    assert client.calls[0]["exchange"] == "NFO"
    assert client.calls[0]["transaction_type"] == "BUY"
    assert client.calls[0]["order_type"] == "MARKET"
    assert client.calls[0]["product"] == "MIS"
    assert client.calls[0]["validity"] == "DAY"
    assert client.order_history_calls == ["fake-order-1"]
    assert market.paper_trade_ledger.count == 0


@pytest.mark.parametrize(
    ("buy_action", "exit_action", "side", "quantity"),
    [
        (SignalAction.BUY_CE, SignalAction.EXIT_CE, "CE", 75),
        (SignalAction.BUY_PE, SignalAction.EXIT_PE, "PE", 50),
    ],
)
def test_live_exit_uses_original_live_contract_context(
    buy_action,
    exit_action,
    side,
    quantity,
):
    market, client = make_market()
    set_strategy_position(market, side, market.latest_option_premiums[side])
    market._execute_strategy_result(result(buy_action), TIME)
    market.latest_option_premiums[side] += 2
    set_strategy_position(market, None, None)

    order_ids = market._execute_strategy_result(
        result(exit_action),
        TIME + timedelta(minutes=1),
    )

    assert order_ids == ("fake-order-2",)
    assert client.calls[-1]["transaction_type"] == "SELL"
    assert client.calls[-1]["tradingsymbol"] == f"NIFTY2682125000{side}"
    assert client.calls[-1]["quantity"] == quantity
    assert market.live_execution_context is None
    assert market.paper_trade_ledger.count == 0
    assert client.order_history_calls == ["fake-order-1", "fake-order-2"]


@pytest.mark.parametrize(
    ("entry_action", "exit_action", "entry_side", "next_action", "next_side"),
    [
        (SignalAction.BUY_CE, SignalAction.EXIT_CE, "CE", SignalAction.BUY_PE, "PE"),
        (SignalAction.BUY_PE, SignalAction.EXIT_PE, "PE", SignalAction.BUY_CE, "CE"),
    ],
)
def test_live_reversal_submits_exit_then_buy(
    entry_action,
    exit_action,
    entry_side,
    next_action,
    next_side,
):
    market, client = make_market()
    set_strategy_position(
        market,
        entry_side,
        market.latest_option_premiums[entry_side],
    )
    market._execute_strategy_result(result(entry_action), TIME)
    set_strategy_position(
        market,
        next_side,
        market.latest_option_premiums[next_side],
    )

    order_ids = market._execute_strategy_result(
        result(exit_action, (exit_action, next_action)),
        TIME + timedelta(minutes=1),
    )

    assert order_ids == ("fake-order-2", "fake-order-3")
    assert [call["transaction_type"] for call in client.calls[-2:]] == [
        "SELL",
        "BUY",
    ]
    assert market.live_execution_context.side == next_side
    assert market.paper_trade_ledger.count == 0


def test_live_target_exit_submits_only_sell_without_paper_accounting():
    market, client = make_market()
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    market.latest_completed_snapshot = IndicatorSnapshot(
        candle=Candle(TIME, 100, 100, 100, 100),
        values={"ema": {}},
    )
    market.latest_option_premiums["CE"] = 29.0

    strategy_result = market._monitor_live_option_target(
        TIME + timedelta(minutes=1)
    )

    assert strategy_result.action is SignalAction.EXIT_CE
    assert [call["transaction_type"] for call in client.calls] == ["BUY", "SELL"]
    assert market.paper_trade_ledger.count == 0
    assert market.live_execution_context is None


def test_live_non_filled_target_exit_retains_context():
    market, client = make_market()
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    active_context = market.live_execution_context
    client.status_records.append(
        {"status": "OPEN", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0}
    )
    market.latest_completed_snapshot = IndicatorSnapshot(
        candle=Candle(TIME, 100, 100, 100, 100),
        values={"ema": {}},
    )
    market.latest_option_premiums["CE"] = 29.0

    market._monitor_live_option_target(TIME + timedelta(minutes=1))

    assert [call["transaction_type"] for call in client.calls] == ["BUY", "SELL"]
    assert market.live_execution_context is active_context
    assert market.pending_live_order.action is SignalAction.EXIT_CE
    assert market.paper_trade_ledger.count == 0


def test_live_disabled_coordinator_fails_closed_without_broker_call():
    market, client = make_market(enabled=False)
    set_strategy_position(market, "CE", 27.0)

    with pytest.raises(LiveExecutionDisabledError):
        market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)

    assert client.calls == []
    assert market.live_execution_context is None


@pytest.mark.parametrize(
    "status_record",
    [
        {"status": "OPEN", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0},
        {"status": "VALIDATION PENDING", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0},
        {"status": "UNRECOGNIZED", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0},
        {"status": "REJECTED", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0},
        {"status": "CANCELLED", "filled_quantity": 25, "pending_quantity": 50, "average_price": 27.0},
        {"status": "COMPLETE", "filled_quantity": 25, "pending_quantity": 0, "average_price": 27.0},
        {"status": "COMPLETE", "filled_quantity": 75, "pending_quantity": 1, "average_price": 27.0},
        {"status": "COMPLETE", "filled_quantity": 0, "pending_quantity": 0, "average_price": 0.0},
    ],
)
def test_live_non_filled_buy_does_not_create_context(status_record):
    market, client = make_market(status_records=[status_record])
    set_strategy_position(market, "CE", 27.0)

    assert market._execute_strategy_result(result(SignalAction.BUY_CE), TIME) == (
        "fake-order-1",
    )
    assert client.order_history_calls == ["fake-order-1"]
    assert market.live_execution_context is None
    assert (market.pending_live_order is not None) is (
        status_record["status"] not in ("REJECTED",)
        and not (
            status_record["status"] == "CANCELLED"
            and status_record["filled_quantity"] == 0
        )
        and not (
            status_record["status"] == "COMPLETE"
            and status_record["filled_quantity"] == 0
            and status_record["pending_quantity"] == 0
        )
    )
    assert market.paper_trade_ledger.count == 0


@pytest.mark.parametrize(
    "exit_status",
    [
        {"status": "OPEN", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0},
        {"status": "VALIDATION PENDING", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0},
        {"status": "UNRECOGNIZED", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0},
        {"status": "REJECTED", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0},
        {"status": "CANCELLED", "filled_quantity": 25, "pending_quantity": 50, "average_price": 27.0},
        {"status": "COMPLETE", "filled_quantity": 25, "pending_quantity": 0, "average_price": 27.0},
        {"status": "COMPLETE", "filled_quantity": 75, "pending_quantity": 1, "average_price": 27.0},
    ],
)
def test_live_non_filled_exit_retains_context(exit_status):
    market, client = make_market()
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    active_context = market.live_execution_context
    client.status_records.append(exit_status)
    set_strategy_position(market, None, None)

    assert market._execute_strategy_result(
        result(SignalAction.EXIT_CE),
        TIME + timedelta(minutes=1),
    ) == ("fake-order-2",)
    assert client.order_history_calls == ["fake-order-1", "fake-order-2"]
    assert market.live_execution_context is active_context
    assert (market.pending_live_order is not None) is (
        exit_status["status"] not in ("REJECTED",)
        and not (
            exit_status["status"] == "CANCELLED"
            and exit_status["filled_quantity"] == 0
        )
        and not (
            exit_status["status"] == "COMPLETE"
            and exit_status["filled_quantity"] == 0
            and exit_status["pending_quantity"] == 0
        )
    )


@pytest.mark.parametrize(
    ("buy_action", "exit_action", "side"),
    [
        (SignalAction.BUY_CE, SignalAction.EXIT_PE, "CE"),
        (SignalAction.BUY_PE, SignalAction.EXIT_CE, "PE"),
    ],
)
def test_live_wrong_side_exit_is_rejected_without_mutating_context(
    buy_action,
    exit_action,
    side,
):
    market, client = make_market()
    set_strategy_position(market, side, market.latest_option_premiums[side])
    market._execute_strategy_result(result(buy_action), TIME)
    active_context = market.live_execution_context
    set_strategy_position(market, None, None)

    with pytest.raises(ValueError, match="does not match"):
        market._execute_strategy_result(
            result(exit_action),
            TIME + timedelta(minutes=1),
        )

    assert len(client.calls) == 1
    assert market.live_execution_context is active_context


def test_live_duplicate_buy_is_rejected_without_mutating_context():
    market, client = make_market()
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    active_context = market.live_execution_context

    with pytest.raises(ValueError, match="already active"):
        market._execute_strategy_result(
            result(SignalAction.BUY_PE),
            TIME + timedelta(minutes=1),
        )

    assert len(client.calls) == 1
    assert market.live_execution_context is active_context


def test_live_hold_warmup_and_duplicate_candle_submit_nothing():
    market, client = make_market()

    assert market._execute_strategy_result(result(SignalAction.HOLD), TIME) == ()
    history = pd.DataFrame(
        [
            {
                "date": TIME + timedelta(minutes=minute),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0 + minute,
                "volume": 0,
            }
            for minute in range(3)
        ]
    )
    market.warm_indicators_from_dataframe(history)
    candle = Candle(TIME + timedelta(minutes=10), 100, 100, 100, 100)

    assert market._process_completed_candle(candle)["processed"] is True
    assert market._process_completed_candle(candle)["processed"] is False
    assert client.calls == []
    assert client.order_history_calls == []


def test_live_broker_failure_propagates_without_retry():
    client = FakeKiteClient(exception=RuntimeError("broker unavailable"))
    market, client = make_market(client=client)
    set_strategy_position(market, "CE", 27.0)

    with pytest.raises(RuntimeError, match="broker unavailable"):
        market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)

    assert len(client.calls) == 1
    assert client.order_history_calls == []
    assert market.live_execution_context is None


def test_live_failed_exit_preserves_existing_submission_context():
    market, client = make_market()
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    active_context = market.live_execution_context
    client.exception = RuntimeError("broker unavailable")
    set_strategy_position(market, None, None)

    with pytest.raises(RuntimeError, match="broker unavailable"):
        market._execute_strategy_result(
            result(SignalAction.EXIT_CE),
            TIME + timedelta(minutes=1),
        )

    assert len(client.calls) == 2
    assert market.live_execution_context is active_context


def test_live_status_read_failure_preserves_context_without_retry():
    market, client = make_market()
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    active_context = market.live_execution_context
    client.status_exception = RuntimeError("status unavailable")
    set_strategy_position(market, None, None)

    with pytest.raises(RuntimeError, match="status unavailable"):
        market._execute_strategy_result(
            result(SignalAction.EXIT_CE),
            TIME + timedelta(minutes=1),
        )

    assert client.order_history_calls == ["fake-order-1", "fake-order-2"]
    assert market.live_execution_context is active_context


def test_live_buy_status_read_failure_leaves_context_none_without_retry():
    market, client = make_market(status_exception=RuntimeError("status unavailable"))
    set_strategy_position(market, "CE", 27.0)

    with pytest.raises(RuntimeError, match="status unavailable"):
        market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)

    assert client.calls == [
        {
            "variety": "regular",
            "tradingsymbol": "NIFTY2682125000CE",
            "exchange": "NFO",
            "transaction_type": "BUY",
            "quantity": 75,
            "order_type": "MARKET",
            "product": "MIS",
            "validity": "DAY",
        }
    ]
    assert client.order_history_calls == ["fake-order-1"]
    assert market.live_execution_context is None


def test_live_non_filled_reversal_does_not_submit_opposite_buy():
    market, client = make_market()
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    active_context = market.live_execution_context
    client.status_records.append(
        {"status": "OPEN", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0}
    )
    set_strategy_position(market, "PE", 31.0)

    assert market._execute_strategy_result(
        result(
            SignalAction.EXIT_CE,
            (SignalAction.EXIT_CE, SignalAction.BUY_PE),
        ),
        TIME + timedelta(minutes=1),
    ) == ("fake-order-2",)
    assert [call["transaction_type"] for call in client.calls] == ["BUY", "SELL"]
    assert market.live_execution_context is active_context


def test_partial_complete_reversal_exit_does_not_submit_opposite_buy():
    market, client = make_market()
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    active_context = market.live_execution_context
    client.status_records.append(
        {"status": "COMPLETE", "filled_quantity": 25, "pending_quantity": 0, "average_price": 27.0}
    )
    set_strategy_position(market, "PE", 31.0)

    market._execute_strategy_result(
        result(
            SignalAction.EXIT_CE,
            (SignalAction.EXIT_CE, SignalAction.BUY_PE),
        ),
        TIME + timedelta(minutes=1),
    )

    assert [call["transaction_type"] for call in client.calls] == ["BUY", "SELL"]
    assert market.pending_live_order.action is SignalAction.EXIT_CE
    assert market.live_execution_context is active_context


@pytest.mark.parametrize(
    "status_record",
    [
        {"status": "OPEN", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0},
        {"status": "VALIDATION PENDING", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0},
        {"status": "UNRECOGNIZED", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0},
        {"status": "CANCELLED", "filled_quantity": 25, "pending_quantity": 50, "average_price": 27.0},
        {"status": "COMPLETE", "filled_quantity": 25, "pending_quantity": 0, "average_price": 27.0},
        {"status": "COMPLETE", "filled_quantity": 75, "pending_quantity": 1, "average_price": 27.0},
    ],
)
def test_live_unresolved_buy_creates_pending_order(status_record):
    market, client = make_market(status_records=[status_record])
    set_strategy_position(market, "CE", 27.0)

    assert market._execute_strategy_result(result(SignalAction.BUY_CE), TIME) == (
        "fake-order-1",
    )
    pending = market.pending_live_order
    assert pending.order_id == "fake-order-1"
    assert pending.action is SignalAction.BUY_CE
    assert pending.side == "CE"
    assert pending.contract_symbol == "NIFTY2682125000CE"
    assert pending.quantity == 75
    assert pending.created_time == TIME
    assert market.live_execution_context is None
    assert client.order_history_calls == ["fake-order-1"]


@pytest.mark.parametrize(
    "status_record",
    [
        {"status": "REJECTED", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0},
        {"status": "CANCELLED", "filled_quantity": 0, "pending_quantity": 0, "average_price": 0.0},
        {"status": "COMPLETE", "filled_quantity": 0, "pending_quantity": 0, "average_price": 0.0},
    ],
)
def test_live_definitive_no_fill_buy_creates_no_pending_order(status_record):
    market, _ = make_market(status_records=[status_record])
    set_strategy_position(market, "CE", 27.0)

    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)

    assert market.pending_live_order is None
    assert market.live_execution_context is None


def test_live_unresolved_exit_creates_pending_and_retains_context():
    market, client = make_market()
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    active_context = market.live_execution_context
    client.status_records.append(
        {"status": "OPEN", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0}
    )
    set_strategy_position(market, None, None)

    market._execute_strategy_result(result(SignalAction.EXIT_CE), TIME)

    assert market.pending_live_order.action is SignalAction.EXIT_CE
    assert market.live_execution_context is active_context


@pytest.mark.parametrize(
    "action",
    [SignalAction.BUY_PE, SignalAction.EXIT_CE],
)
def test_pending_live_order_blocks_new_actions_without_broker_work(action):
    market, client = make_market(
        status_records=[
            {"status": "OPEN", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0}
        ]
    )
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    pending = market.pending_live_order
    calls_before = len(client.calls)
    reads_before = len(client.order_history_calls)

    with pytest.raises(PendingLiveOrderError, match="pending LIVE order"):
        market._execute_strategy_result(result(action), TIME + timedelta(minutes=1))

    assert len(client.calls) == calls_before
    assert len(client.order_history_calls) == reads_before
    assert market.pending_live_order is pending
    assert market.live_execution_context is None


def test_hold_remains_a_no_op_while_live_order_is_pending():
    market, client = make_market(
        status_records=[
            {"status": "OPEN", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0}
        ]
    )
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)

    assert market._execute_strategy_result(result(SignalAction.HOLD), TIME) == ()
    assert len(client.calls) == 1
    assert len(client.order_history_calls) == 1


def test_pending_exit_blocks_buy_without_another_broker_submission():
    market, client = make_market()
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    client.status_records.append(
        {"status": "OPEN", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0}
    )
    set_strategy_position(market, None, None)
    market._execute_strategy_result(result(SignalAction.EXIT_CE), TIME)
    pending = market.pending_live_order
    calls_before = len(client.calls)
    reads_before = len(client.order_history_calls)

    with pytest.raises(PendingLiveOrderError):
        market._execute_strategy_result(result(SignalAction.BUY_PE), TIME)

    assert market.pending_live_order is pending
    assert len(client.calls) == calls_before
    assert len(client.order_history_calls) == reads_before


def test_pending_target_exit_blocks_a_duplicate_sell():
    market, client = make_market()
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    client.status_records.append(
        {"status": "OPEN", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0}
    )
    market.latest_completed_snapshot = IndicatorSnapshot(
        candle=Candle(TIME, 100, 100, 100, 100),
        values={"ema": {}},
    )
    market.latest_option_premiums["CE"] = 29.0
    market._monitor_live_option_target(TIME + timedelta(minutes=1))
    calls_before = len(client.calls)
    reads_before = len(client.order_history_calls)

    with pytest.raises(PendingLiveOrderError):
        market._execute_strategy_result(result(SignalAction.EXIT_CE), TIME)

    assert market.pending_live_order.action is SignalAction.EXIT_CE
    assert len(client.calls) == calls_before
    assert len(client.order_history_calls) == reads_before


def test_reconcile_pending_buy_complete_creates_context_without_submission():
    market, client = make_market(
        status_records=[
            {"status": "OPEN", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0}
        ]
    )
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    replace_status(
        client,
        "fake-order-1",
        {"status": "COMPLETE", "filled_quantity": 75, "pending_quantity": 0, "average_price": 27.0},
    )

    status = market.reconcile_pending_live_order()

    assert status.is_filled is True
    assert len(client.calls) == 1
    assert client.order_history_calls == ["fake-order-1", "fake-order-1"]
    assert market.pending_live_order is None
    assert market.live_execution_context.side == "CE"


def test_reconcile_pending_buy_partial_complete_keeps_pending_without_context():
    market, client = make_market(
        status_records=[
            {"status": "OPEN", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0}
        ]
    )
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    pending = market.pending_live_order
    replace_status(
        client,
        "fake-order-1",
        {"status": "COMPLETE", "filled_quantity": 25, "pending_quantity": 0, "average_price": 27.0},
    )

    market.reconcile_pending_live_order()

    assert market.pending_live_order == pending
    assert market.live_execution_context is None
    assert len(client.calls) == 1


def test_reconcile_pending_exit_complete_clears_context_without_reversal_buy():
    market, client = make_market()
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    client.status_records.append(
        {"status": "OPEN", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0}
    )
    set_strategy_position(market, "PE", 31.0)
    market._execute_strategy_result(
        result(SignalAction.EXIT_CE, (SignalAction.EXIT_CE, SignalAction.BUY_PE)),
        TIME + timedelta(minutes=1),
    )
    replace_status(
        client,
        "fake-order-2",
        {"status": "COMPLETE", "filled_quantity": 75, "pending_quantity": 0, "average_price": 27.0},
    )

    market.reconcile_pending_live_order()

    assert [call["transaction_type"] for call in client.calls] == ["BUY", "SELL"]
    assert market.pending_live_order is None
    assert market.live_execution_context is None


def test_reconcile_pending_exit_partial_complete_keeps_context_and_pending():
    market, client = make_market()
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    active_context = market.live_execution_context
    client.status_records.append(
        {"status": "OPEN", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0}
    )
    set_strategy_position(market, None, None)
    market._execute_strategy_result(result(SignalAction.EXIT_CE), TIME)
    pending = market.pending_live_order
    replace_status(
        client,
        "fake-order-2",
        {"status": "COMPLETE", "filled_quantity": 25, "pending_quantity": 0, "average_price": 27.0},
    )

    market.reconcile_pending_live_order()

    assert market.pending_live_order == pending
    assert market.live_execution_context is active_context
    assert len(client.calls) == 2


@pytest.mark.parametrize(
    "status_record",
    [
        {"status": "OPEN", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0},
        {"status": "VALIDATION PENDING", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0},
        {"status": "UNRECOGNIZED", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0},
        {"status": "CANCELLED", "filled_quantity": 25, "pending_quantity": 50, "average_price": 27.0},
    ],
)
def test_reconcile_unresolved_status_retains_pending_and_context(status_record):
    market, client = make_market()
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    active_context = market.live_execution_context
    client.status_records.append(
        {"status": "OPEN", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0}
    )
    set_strategy_position(market, None, None)
    market._execute_strategy_result(result(SignalAction.EXIT_CE), TIME)
    pending = market.pending_live_order
    replace_status(client, "fake-order-2", status_record)

    market.reconcile_pending_live_order()

    assert len(client.calls) == 2
    assert market.pending_live_order == pending
    assert market.live_execution_context is active_context


@pytest.mark.parametrize(
    "status_record",
    [
        {"status": "REJECTED", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0},
        {"status": "CANCELLED", "filled_quantity": 0, "pending_quantity": 0, "average_price": 0.0},
    ],
)
def test_reconcile_definitive_no_fill_clears_pending_but_retains_context(status_record):
    market, client = make_market()
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    active_context = market.live_execution_context
    client.status_records.append(
        {"status": "OPEN", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0}
    )
    set_strategy_position(market, None, None)
    market._execute_strategy_result(result(SignalAction.EXIT_CE), TIME)
    replace_status(client, "fake-order-2", status_record)

    market.reconcile_pending_live_order()

    assert market.pending_live_order is None
    assert market.live_execution_context is active_context


def test_reconcile_read_failure_preserves_pending_and_context_without_retry():
    market, client = make_market()
    set_strategy_position(market, "CE", 27.0)
    market._execute_strategy_result(result(SignalAction.BUY_CE), TIME)
    active_context = market.live_execution_context
    client.status_records.append(
        {"status": "OPEN", "filled_quantity": 0, "pending_quantity": 75, "average_price": 0.0}
    )
    set_strategy_position(market, None, None)
    market._execute_strategy_result(result(SignalAction.EXIT_CE), TIME)
    pending = market.pending_live_order
    client.status_exception = RuntimeError("status unavailable")

    with pytest.raises(RuntimeError, match="status unavailable"):
        market.reconcile_pending_live_order()

    assert len(client.calls) == 2
    assert client.order_history_calls == ["fake-order-1", "fake-order-2", "fake-order-2"]
    assert market.pending_live_order is pending
    assert market.live_execution_context is active_context


def test_reconcile_without_pending_order_is_a_clear_state_error():
    market, client = make_market()

    with pytest.raises(ValueError, match="No pending LIVE order"):
        market.reconcile_pending_live_order()

    assert client.calls == []
    assert client.order_history_calls == []
