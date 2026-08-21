from trading.execution_mode import ExecutionMode


def test_execution_modes_are_explicit():
    assert ExecutionMode.PAPER.value == "PAPER"
    assert ExecutionMode.LIVE.value == "LIVE"
