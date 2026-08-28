"""백테스트 온전성.

전략이 좋은지가 아니라, 엔진이 거짓말을 안 하는지를 본다.
"""
from __future__ import annotations

import pytest

from marketlens.backtest import engine
from tests.conftest import make_candles


def always_long(_window):
    return 1


def always_flat(_window):
    return 0


def test_always_long_with_no_cost_matches_buy_and_hold(candles):
    """수수료 0으로 계속 들고 있으면 단순 보유와 같아야 한다.

    여기서 갈라지면 체결 시점이나 수익률 누적이 틀린 것이다.
    """
    result = engine.run(candles, always_long, warmup=120, fee=0.0, slippage=0.0)
    total = result.equity.iloc[-1] - 1.0
    assert total == pytest.approx(result.buy_hold, rel=1e-9)


def test_flat_strategy_never_trades(candles):
    result = engine.run(candles, always_flat, warmup=120)
    assert result.trades == []
    assert result.equity.iloc[-1] == pytest.approx(1.0)


def test_costs_reduce_returns(candles):
    """수수료를 물리면 같은 전략의 성적이 반드시 나빠져야 한다."""
    free = engine.run(candles, always_long, warmup=120, fee=0.0, slippage=0.0)
    paid = engine.run(candles, always_long, warmup=120, fee=0.002, slippage=0.002)
    assert paid.trades[0].ret < free.trades[0].ret


def test_entry_fills_on_the_next_bar_open(candles):
    """봉 i 의 판단은 봉 i+1 의 시가에 체결된다.

    같은 봉의 종가에 체결하면 그 종가를 보고 판단한 셈이 되어 성적이 부풀려진다.
    """
    warmup = 120
    result = engine.run(candles, always_long, warmup=warmup, fee=0.0, slippage=0.0)
    assert result.trades
    first = result.trades[0]
    expected_ts = int(candles["ts"].iloc[warmup + 1])
    assert first.entry_ts == expected_ts
    assert first.entry == pytest.approx(float(candles["open"].iloc[warmup + 1]))


def test_short_history_returns_empty_result():
    result = engine.run(make_candles(count=30), always_long, warmup=120)
    assert result.trades == []
    assert len(result.equity) == 0


def test_no_short_flag_blocks_negative_positions(candles):
    result = engine.run(candles, lambda _w: -1, warmup=120, allow_short=False)
    assert result.trades == []


def test_metrics_shape(candles):
    payload = engine.run(candles, always_long, warmup=120).to_dict()
    metrics = payload["metrics"]
    assert set(metrics) >= {"trades", "winRate", "profitFactor", "maxDrawdown",
                            "totalReturn", "sharpe", "payoff"}
    assert metrics["maxDrawdown"] <= 0.0
    assert payload["equity"][0]["time"] < payload["equity"][-1]["time"]


def test_signal_strategy_runs_end_to_end(short_candles):
    """실제 전략(시그널 엔진)이 붙어도 도는지. 느리므로 짧은 데이터로만."""
    result = engine.run(short_candles, engine.signal_strategy(), warmup=90)
    assert len(result.equity) == len(short_candles)
    assert result.to_dict()["metrics"]["trades"] >= 0
