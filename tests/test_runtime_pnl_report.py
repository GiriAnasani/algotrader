import json
from dataclasses import FrozenInstanceError, fields
from datetime import date, datetime
from math import inf, nan
from pathlib import Path

import pytest

from trading.runtime_pnl import RuntimePnLSnapshot
from trading.runtime_pnl_report import RuntimePnLReport, RuntimePnLReporter


DAY = date(2026, 8, 24)


def report(**overrides):
    values = dict(trading_date=DAY, closed_trade_count=2, open_position_count=1,
                  realized_gross_pnl=500.0, realized_total_charges=90.0,
                  realized_net_pnl=410.0, unrealized_gross_pnl=-50.0,
                  portfolio_net_pnl=360.0, session_net_pnl=200.0)
    values.update(overrides)
    return RuntimePnLReport(**values)


def snapshot(**overrides):
    values = dict(trading_date=DAY, closed_trade_count=2, open_position_count=1,
                  realized_gross_pnl=500.0, realized_total_charges=90.0,
                  realized_net_pnl=410.0, unrealized_gross_pnl=-50.0,
                  portfolio_net_pnl=360.0, session_net_pnl=200.0)
    values.update(overrides)
    return RuntimePnLSnapshot(**values)


def test_reporter_constructs_without_dependencies():
    assert isinstance(RuntimePnLReporter(), RuntimePnLReporter)


@pytest.mark.parametrize("value", [None, object(), True, "snapshot"])
def test_build_requires_runtime_snapshot(value):
    with pytest.raises(TypeError):
        RuntimePnLReporter().build(value)


def test_report_is_frozen_with_exact_fields():
    value = report()
    assert [field.name for field in fields(value)] == [
        "trading_date", "closed_trade_count", "open_position_count",
        "realized_gross_pnl", "realized_total_charges", "realized_net_pnl",
        "unrealized_gross_pnl", "portfolio_net_pnl", "session_net_pnl"]
    with pytest.raises(FrozenInstanceError):
        value.session_net_pnl = 0


@pytest.mark.parametrize("invalid", [None, "2026-08-24", datetime(2026, 8, 24), True])
def test_report_requires_strict_date(invalid):
    with pytest.raises(TypeError):
        report(trading_date=invalid)


@pytest.mark.parametrize("field,bad", [
    ("closed_trade_count", -1), ("closed_trade_count", True),
    ("closed_trade_count", 1.5), ("open_position_count", -1),
    ("open_position_count", 2), ("open_position_count", True)])
def test_report_rejects_invalid_counts(field, bad):
    with pytest.raises(ValueError):
        report(**{field: bad})


@pytest.mark.parametrize("field", ["realized_gross_pnl", "realized_total_charges",
    "realized_net_pnl", "unrealized_gross_pnl", "portfolio_net_pnl",
    "session_net_pnl"])
@pytest.mark.parametrize("bad", [True, nan, inf, -inf, object()])
def test_report_rejects_invalid_numeric_values(field, bad):
    with pytest.raises(ValueError):
        report(**{field: bad})


def test_report_rejects_negative_charges_and_inconsistent_totals():
    with pytest.raises(ValueError):
        report(realized_total_charges=-1)
    with pytest.raises(ValueError):
        report(realized_net_pnl=411)
    with pytest.raises(ValueError):
        report(portfolio_net_pnl=361)


def test_reporter_copies_every_snapshot_value_exactly():
    source = snapshot(session_net_pnl=-123.456789)
    value = RuntimePnLReporter().build(source)
    assert value.trading_date is source.trading_date
    assert value.closed_trade_count == source.closed_trade_count
    assert value.open_position_count == source.open_position_count
    assert value.realized_gross_pnl == source.realized_gross_pnl
    assert value.realized_total_charges == source.realized_total_charges
    assert value.realized_net_pnl == source.realized_net_pnl
    assert value.unrealized_gross_pnl == source.unrealized_gross_pnl
    assert value.portfolio_net_pnl == source.portfolio_net_pnl
    assert value.session_net_pnl == source.session_net_pnl


def test_unusual_valid_values_are_preserved_without_rounding():
    source = RuntimePnLSnapshot(
        DAY, 7, 1, 0.123456789, 0.023456789, 0.1,
        -0.333333333, -0.233333333, 987.654321)
    value = RuntimePnLReporter().build(source)
    assert value.realized_gross_pnl == 0.123456789
    assert value.realized_total_charges == 0.023456789
    assert value.session_net_pnl == 987.654321


def test_to_dict_has_exact_keys_iso_date_and_numeric_types():
    document = report().to_dict()
    assert document == {
        "trading_date": "2026-08-24", "closed_trade_count": 2,
        "open_position_count": 1, "realized_gross_pnl": 500.0,
        "realized_total_charges": 90.0, "realized_net_pnl": 410.0,
        "unrealized_gross_pnl": -50.0, "portfolio_net_pnl": 360.0,
        "session_net_pnl": 200.0}
    assert type(document["closed_trade_count"]) is int
    assert type(document["realized_net_pnl"]) is float


def test_to_dict_is_fresh_and_mutation_cannot_change_report():
    value = report()
    first = value.to_dict()
    second = value.to_dict()
    assert first == second and first is not second
    first["realized_net_pnl"] = 0
    first["extra"] = True
    assert value.realized_net_pnl == 410.0
    assert value.to_dict() == second


def test_to_json_is_deterministic_valid_and_matches_dict():
    value = report()
    first = value.to_json()
    assert first == value.to_json()
    assert json.loads(first) == value.to_dict()
    assert "NaN" not in first and "Infinity" not in first


def test_build_and_serialization_do_not_mutate_snapshot():
    source = snapshot()
    before = tuple(getattr(source, field.name) for field in fields(source))
    RuntimePnLReporter().build(source).to_dict()
    assert tuple(getattr(source, field.name) for field in fields(source)) == before


def test_module_has_only_snapshot_reporting_dependencies_and_no_side_effects():
    source = Path("trading/runtime_pnl_report.py").read_text(encoding="utf-8").lower()
    forbidden = ("marketdata", "positionmanager", "positionstore",
                 "closedpositionhistory", "runtimepnlstateadapter", "kite",
                 "zerodha", "websocket", "price", "strategy", "readiness",
                 "authorization", "execution_enabled", "risk", "capital", "margin",
                 "database", "open(", "write(", "http", "server", "print(",
                 "logging", "positionpnlcalculator", "netpnlcalculator", "aggregator")
    assert all(term not in source for term in forbidden)
