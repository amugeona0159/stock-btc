"""시그널 엔진 — 특히 리페인팅.

여기가 깨지면 백테스트 숫자가 전부 거짓말이 된다. 지표가 틀린 것보다 나쁘다.
"""
from __future__ import annotations

import pytest

from marketlens.core.candle import Candle, upsert
from marketlens.signals.engine import LABELS, evaluate
from marketlens.signals.rules import all_rules, context
from tests.conftest import make_candles


def test_signal_has_a_direction_and_reasons(candles):
    signal = evaluate(candles)
    assert signal.direction in (-1, 0, 1)
    assert signal.label == LABELS[signal.direction]
    assert 0.0 <= signal.confidence <= 1.0
    assert signal.hits, "규칙이 하나도 안 켜졌다 — 400봉이면 충분해야 한다"
    for hit in signal.hits:
        assert hit.reason.strip(), f"{hit.key} 의 근거가 비었다"


def test_signal_ignores_the_forming_bar(candles):
    """미확정 봉을 붙여도 판단이 바뀌면 안 된다.

    바뀐다면 장중에 신호가 떴다 사라진다는 뜻이고, 그 신호로는 아무것도 못 한다.
    """
    baseline = evaluate(candles)
    step = int(candles["ts"].iloc[1] - candles["ts"].iloc[0])
    # 마지막 봉 다음에 말도 안 되게 튄 미확정 봉을 붙인다.
    spike = Candle(int(candles["ts"].iloc[-1]) + step,
                   float(candles["close"].iloc[-1]),
                   float(candles["close"].iloc[-1]) * 1.5,
                   float(candles["close"].iloc[-1]) * 0.5,
                   float(candles["close"].iloc[-1]) * 1.4,
                   99999.0, closed=False)
    with_forming = upsert(candles, spike)

    live = evaluate(with_forming)
    assert live.direction == baseline.direction
    assert live.score == pytest.approx(baseline.score)


def test_streaming_matches_batch(candles):
    """봉을 하나씩 흘려 넣어도 확정봉 판단은 한 번에 넣은 것과 같아야 한다.

    지표가 창 전체를 다시 보는 대신 어딘가에 상태를 들고 있으면 여기서 갈라진다.
    """
    cutoff = len(candles) - 12
    batch = [evaluate(candles.iloc[: i + 1]).direction for i in range(cutoff, len(candles))]

    streamed = []
    running = candles.iloc[:cutoff].copy()
    for i in range(cutoff, len(candles)):
        row = candles.iloc[i]
        running = upsert(running, Candle(
            int(row.ts), float(row.open), float(row.high), float(row.low),
            float(row.close), float(row.volume), closed=True,
        ))
        streamed.append(evaluate(running).direction)

    assert streamed == batch


def test_empty_frame_is_neutral():
    from marketlens.core.candle import empty_frame
    signal = evaluate(empty_frame())
    assert signal.direction == 0
    assert signal.hits == []


def test_short_history_does_not_crash():
    """새 종목을 처음 열면 늘 이 상태를 지난다."""
    signal = evaluate(make_candles(count=8, seed=3))
    assert signal.direction in (-1, 0, 1)


@pytest.mark.parametrize("key,fn", all_rules(), ids=[k for k, _ in all_rules()])
def test_every_rule_returns_a_usable_hit(key, fn, candles):
    hit = fn(context(candles))
    if hit is None:
        return
    assert hit.direction in (-1, 0, 1)
    assert 0.0 <= hit.strength <= 1.0
    assert hit.weight > 0
    assert hit.reason.strip()
    assert hit.key == key or hit.key.startswith(key.split("_")[0])
