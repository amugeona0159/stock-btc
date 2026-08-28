"""규칙들의 결론을 하나로 모은다.

각 규칙은 방향(+1/-1)과 세기(0..1)와 비중을 낸다. 집계는 가중 합이고,
신뢰도는 '얼마나 한쪽으로 쏠렸는가'다 — 규칙이 많이 켜졌다고 신뢰도가 오르지는 않는다.
서로 반대 방향이면 오히려 떨어져야 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..core.candle import closed_only
from . import presets as _presets  # noqa: F401  (import 이 곧 등록이다)
from .rules import Precomputed, RuleHit, all_rules, context, context_at

# 방향 문자열은 한 곳에서만 만든다. 예측 3층이 같은 어휘를 써야 화면이 셋을 구분하지 않는다.
LABELS = {1: "매수", 0: "관망", -1: "매도"}


@dataclass
class Signal:
    direction: int
    label: str
    confidence: float
    score: float
    hits: list[RuleHit] = field(default_factory=list)
    bar_ts: int | None = None

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "label": self.label,
            "confidence": round(self.confidence, 3),
            "score": round(self.score, 3),
            "barTs": self.bar_ts,
            "reasons": [
                {
                    "key": h.key,
                    "label": h.label,
                    "direction": h.direction,
                    "strength": round(h.strength, 3),
                    "weight": h.weight,
                    "text": h.reason,
                }
                for h in self.hits
            ],
        }


def evaluate(df: pd.DataFrame, threshold: float = 0.15) -> Signal:
    """확정된 마지막 봉 기준의 종합 판단."""
    closed = closed_only(df)
    if closed.empty:
        return Signal(0, LABELS[0], 0.0, 0.0, [], None)
    return _evaluate_context(context(closed), int(closed["ts"].iloc[-1]), threshold)


def _evaluate_context(ctx, bar_ts: int, threshold: float) -> Signal:
    hits: list[RuleHit] = []
    for _, fn in all_rules():
        try:
            hit = fn(ctx)
        except Exception:
            # 규칙 하나가 터져도 판단 전체를 멈추지 않는다. 데워지지 않은 지표가 흔하다.
            continue
        if hit is not None:
            hits.append(hit)

    total_weight = sum(h.weight for h in hits)
    if total_weight <= 0:
        return Signal(0, LABELS[0], 0.0, 0.0, hits, bar_ts)

    score = sum(h.direction * h.strength * h.weight for h in hits) / total_weight
    direction = 1 if score > threshold else (-1 if score < -threshold else 0)
    # 관망일 때의 신뢰도는 '관망이라는 판단'의 신뢰도가 아니라 쏠림의 크기다.
    # 그대로 두면 0.02 짜리 쏠림이 "신뢰도 2%" 로 나가 읽는 사람을 헷갈리게 한다.
    confidence = min(1.0, abs(score) * 1.6)

    hits.sort(key=lambda h: -(h.strength * h.weight))
    return Signal(direction, LABELS[direction], confidence, score, hits, bar_ts)


def position_series(df: pd.DataFrame, warmup: int = 120, threshold: float = 0.15) -> pd.Series:
    """봉마다 이 엔진이 무슨 판단을 했을지. 백테스트가 쓴다.

    봉 i 의 판단은 봉 i 까지만 보고 내린다 — 뒤를 잘라서 다시 평가하는 이 방식이
    느린 대신 정직하다. 벡터로 한 번에 계산하면 어디선가 미래를 훔쳐본다.
    """
    closed = closed_only(df).reset_index(drop=True)
    out = pd.Series(0, index=closed.index, dtype="int64")
    source = Precomputed(closed)
    for i in range(warmup, len(closed)):
        ctx = context_at(source, i)
        out.iloc[i] = _evaluate_context(ctx, int(closed["ts"].iloc[i]), threshold).direction
    return out


class PreparedSignalStrategy:
    """백테스트용 시그널 전략.

    `prepare()` 로 전 구간 지표를 한 번만 계산하고, `at(i)` 로 봉 i 까지만 보고 판단한다.
    `prepare` 없이 그냥 호출하면 창을 받아 느린 길로 간다 — 테스트가 두 길이 같은
    답을 내는지 확인한다.
    """

    def __init__(self, threshold: float = 0.15) -> None:
        self.threshold = threshold
        self._source: Precomputed | None = None

    def prepare(self, closed: pd.DataFrame) -> None:
        self._source = Precomputed(closed.reset_index(drop=True))

    def at(self, index: int) -> int:
        if self._source is None:
            raise RuntimeError("prepare() 를 먼저 불러야 한다")
        ts = int(self._source.df["ts"].iloc[index])
        return _evaluate_context(context_at(self._source, index), ts, self.threshold).direction

    def __call__(self, window: pd.DataFrame) -> int:
        return evaluate(window, self.threshold).direction


def prepared_signal_strategy(threshold: float = 0.15) -> PreparedSignalStrategy:
    return PreparedSignalStrategy(threshold)
