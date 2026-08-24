from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
from math import fsum, inf, nan
from pathlib import Path

import pytest

from trading.close_position_lifecycle import ClosedPosition
from trading.option_charges import (
    NetPnLCalculator,
    NetPnLResult,
    OptionChargeSchedule,
    OptionTradeChargesCalculator,
    OptionTradeChargesResult,
    ZERODHA_NSE_OPTIONS_2026,
)
from trading.position import ManagedPosition, PositionSide, PositionState
from trading.position_pnl import PositionPnLCalculator


NOW = datetime(2026, 8, 24, 9, 15, tzinfo=timezone.utc)


def closed(side=PositionSide.CE, entry=100.0, exit=105.0, quantity=65):
    return ClosedPosition(side, f"NIFTY26AUG25000{side.value}", quantity, entry,
                          NOW, exit, NOW, PositionState.CLOSED)


def schedule(**overrides):
    values = dict(brokerage_per_executed_order=20, sell_stt_rate=.0015,
                  exchange_transaction_rate=.0003553, sebi_rate=.000001,
                  stamp_duty_buy_rate=.00003, gst_rate=.18)
    values.update(overrides)
    return OptionChargeSchedule(**values)


def calculator(custom=None):
    return OptionTradeChargesCalculator(custom or schedule())


def valid_result(**overrides):
    values = dict(buy_turnover=10.0, sell_turnover=12.0, total_turnover=22.0,
                  brokerage=1.0, stt=2.0, exchange_transaction_charges=3.0,
                  sebi_charges=4.0, stamp_duty=5.0, gst=6.0,
                  total_charges=21.0)
    values.update(overrides)
    return OptionTradeChargesResult(**values)


def test_schedule_fields_named_values_and_frozen():
    assert [f.name for f in fields(OptionChargeSchedule)] == [
        "brokerage_per_executed_order", "sell_stt_rate",
        "exchange_transaction_rate", "sebi_rate", "stamp_duty_buy_rate", "gst_rate"]
    assert ZERODHA_NSE_OPTIONS_2026 == schedule()
    assert OptionChargeSchedule.zerodha_nse_options_2026() == schedule()
    with pytest.raises(FrozenInstanceError):
        ZERODHA_NSE_OPTIONS_2026.gst_rate = 0


@pytest.mark.parametrize("bad", [True, -1, nan, inf, -inf])
@pytest.mark.parametrize("field", ["brokerage_per_executed_order", "sell_stt_rate",
    "exchange_transaction_rate", "sebi_rate", "stamp_duty_buy_rate", "gst_rate"])
def test_schedule_rejects_invalid_numbers(field, bad):
    with pytest.raises(ValueError):
        schedule(**{field: bad})


@pytest.mark.parametrize("field", ["sell_stt_rate", "exchange_transaction_rate",
                                    "sebi_rate", "stamp_duty_buy_rate", "gst_rate"])
def test_schedule_rejects_rates_at_least_one(field):
    with pytest.raises(ValueError):
        schedule(**{field: 1})


@pytest.mark.parametrize("side", [PositionSide.CE, PositionSide.PE])
def test_current_schedule_calculates_each_component_without_rounding(side):
    result = calculator(ZERODHA_NSE_OPTIONS_2026).calculate(closed(side))
    buy, sell = 100 * 65, 105 * 65
    turnover = fsum((buy, sell))
    brokerage = 20 * 2
    stt = sell * .0015
    transaction = turnover * .0003553
    sebi = turnover * .000001
    stamp = buy * .00003
    gst = fsum((brokerage, transaction, sebi)) * .18
    assert result.buy_turnover == buy
    assert result.sell_turnover == sell
    assert result.total_turnover == turnover
    assert result.brokerage == brokerage
    assert result.stt == pytest.approx(stt)
    assert result.exchange_transaction_charges == pytest.approx(transaction)
    assert result.sebi_charges == pytest.approx(sebi)
    assert result.stamp_duty == pytest.approx(stamp)
    assert result.gst == pytest.approx(gst)
    assert result.total_charges == fsum((brokerage, stt, transaction, sebi, stamp, gst))
    assert result.exchange_transaction_charges != round(transaction, 2)


def test_charge_bases_are_independent_and_brokerage_uses_order_counts():
    custom = schedule(brokerage_per_executed_order=7, sell_stt_rate=.1,
                      exchange_transaction_rate=.2, sebi_rate=.3,
                      stamp_duty_buy_rate=.4, gst_rate=.5)
    result = calculator(custom).calculate(closed(entry=2, exit=3, quantity=5), 2, 4)
    assert result.buy_turnover == 10
    assert result.sell_turnover == 15
    assert result.brokerage == 42
    assert result.stt == 15 * .1
    assert result.exchange_transaction_charges == 25 * .2
    assert result.sebi_charges == 25 * .3
    assert result.stamp_duty == 10 * .4
    assert result.gst == fsum((42, 25 * .2, 25 * .3)) * .5


@pytest.mark.parametrize("count", [True, False, 0, -1, 1.5, "1", None])
@pytest.mark.parametrize("argument", ["buy_order_count", "sell_order_count"])
def test_invalid_order_counts_rejected(argument, count):
    with pytest.raises(ValueError):
        calculator().calculate(closed(), **{argument: count})


@pytest.mark.parametrize("value", [None, object(), True, "position"])
def test_charge_calculator_rejects_non_closed_positions(value):
    with pytest.raises(TypeError):
        calculator().calculate(value)


def test_managed_position_is_rejected():
    managed = ManagedPosition(PositionSide.CE, "NIFTY26AUG25000CE", 65, 100,
                              NOW, PositionState.OPEN)
    with pytest.raises(TypeError):
        calculator().calculate(managed)


@pytest.mark.parametrize("value", [None, object(), True, "schedule"])
def test_charge_calculator_requires_schedule(value):
    with pytest.raises(TypeError):
        OptionTradeChargesCalculator(value)


def test_charge_result_exact_fields_and_frozen():
    result = valid_result()
    assert [f.name for f in fields(result)] == ["buy_turnover", "sell_turnover",
        "total_turnover", "brokerage", "stt", "exchange_transaction_charges",
        "sebi_charges", "stamp_duty", "gst", "total_charges"]
    with pytest.raises(FrozenInstanceError):
        result.gst = 0


@pytest.mark.parametrize("bad", [True, -1, nan, inf, -inf, object()])
def test_charge_result_rejects_invalid_components(bad):
    with pytest.raises(ValueError):
        valid_result(stt=bad)


def test_charge_result_rejects_inconsistent_totals():
    with pytest.raises(ValueError):
        valid_result(total_turnover=23)
    with pytest.raises(ValueError):
        valid_result(total_charges=22)


@pytest.mark.parametrize("side,entry,exit", [
    (PositionSide.CE, 100, 105), (PositionSide.CE, 105, 100),
    (PositionSide.PE, 100, 105), (PositionSide.PE, 105, 100),
    (PositionSide.CE, 100, 100)])
def test_net_pnl_for_wins_losses_and_gross_breakeven(side, entry, exit):
    position = closed(side, entry, exit)
    result = NetPnLCalculator(calculator()).calculate(position)
    gross = PositionPnLCalculator().calculate_closed(position).gross_pnl
    assert result.gross_pnl == gross
    assert result.net_pnl == fsum((gross, -result.total_charges))
    assert result.net_pnl < gross


def test_net_calculator_forwards_exact_position_and_counts(monkeypatch):
    position = closed()
    charge_calculator = calculator()
    seen = []
    original_gross = PositionPnLCalculator.calculate_closed
    original_charges = charge_calculator.calculate
    def gross_spy(self, supplied):
        seen.append(("gross", supplied))
        return original_gross(self, supplied)
    def charge_spy(supplied, buy_count, sell_count):
        seen.append(("charges", supplied, buy_count, sell_count))
        return original_charges(supplied, buy_count, sell_count)
    monkeypatch.setattr(PositionPnLCalculator, "calculate_closed", gross_spy)
    monkeypatch.setattr(charge_calculator, "calculate", charge_spy)
    NetPnLCalculator(charge_calculator).calculate(position, 2, 3)
    assert seen == [("gross", position), ("charges", position, 2, 3)]


@pytest.mark.parametrize("value", [None, object(), True, "calculator"])
def test_net_calculator_requires_charge_calculator(value):
    with pytest.raises(TypeError):
        NetPnLCalculator(value)


def test_net_result_exact_fields_and_frozen():
    result = NetPnLResult(10, 2, 8)
    assert [f.name for f in fields(result)] == ["gross_pnl", "total_charges", "net_pnl"]
    with pytest.raises(FrozenInstanceError):
        result.net_pnl = 0


@pytest.mark.parametrize("gross", [-2, 0, 2])
def test_net_result_accepts_finite_signed_gross(gross):
    assert NetPnLResult(gross, 1, fsum((gross, -1))).gross_pnl == gross


@pytest.mark.parametrize("values", [(nan, 1, 0), (inf, 1, 0), (0, nan, 0),
                                     (0, inf, 0), (0, -1, 1), (0, 1, nan)])
def test_net_result_rejects_invalid_values(values):
    with pytest.raises(ValueError):
        NetPnLResult(*values)


def test_net_result_rejects_inconsistent_net():
    with pytest.raises(ValueError):
        NetPnLResult(10, 2, 7)


def test_module_remains_calculation_only_and_out_of_scope():
    source = Path("trading/option_charges.py").read_text(encoding="utf-8").lower()
    forbidden = ("kite", "zerodha api", "order_history", "contract note",
                 "marketdata", "positionmanager", "positionstore", "readiness",
                 "authorization", "strategy", "websocket", "datetime", "time.",
                 "margin", "collateral", "account balance", "exercise", "expiry",
                 "portfolio", "session", "round(")
    assert all(term not in source for term in forbidden)
