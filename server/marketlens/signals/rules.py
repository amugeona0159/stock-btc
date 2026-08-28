"""규칙의 뼈대.

규칙은 '확정된 봉만' 본다. 미확정 봉을 넣으면 장중에 떴다 사라지는 신호가 되고,
그 순간 백테스트 승률과 실전이 갈라진다 — 그 경계를 여기 한 곳에서 지킨다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from ..core.candle import closed_only
from ..indicators import catalog, patterns
from ..indicators.structure import Swing, find_swings


# 창의 **끝**을 보고 값이 달라지는 지표들. 이건 봉마다 다시 계산해야 한다.
# 나머지는 인과적이라(각 봉의 값이 그 봉까지만 의존) 전 구간을 한 번 계산해 자르면 된다.
NON_CAUSAL = frozenset({"fibonacci", "support_resistance", "linreg_channel", "volume_profile"})


class Precomputed:
    """전 구간 지표를 한 번만 계산해 두고 잘라 쓴다.

    봉마다 전체를 다시 계산하면 백테스트가 봉 수의 제곱으로 늘어난다 —
    600봉에 10초, 5000봉이면 몇 분이다. 인과적 지표는 잘라 써도 값이 같다는 것을
    `test_backtest.py: test_fast_path_matches_slow_path` 가 지킨다.
    """

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df
        self._frames: dict[tuple, pd.DataFrame] = {}
        self._swings: dict[tuple, list[Swing]] = {}
        self._patterns: dict[str, pd.Series] | None = None

    def frame(self, key: str, params: dict) -> pd.DataFrame | None:
        if key in NON_CAUSAL:
            return None
        cache_key = (key, tuple(sorted(params.items())))
        if cache_key not in self._frames:
            self._frames[cache_key] = catalog.compute(key, self.df, params)
        return self._frames[cache_key]

    def patterns(self) -> dict[str, pd.Series]:
        """캔들 패턴은 시프트만 쓰므로 인과적이다. 전 구간 한 번이면 된다."""
        if self._patterns is None:
            self._patterns = patterns.detect(self.df)
        return self._patterns

    def swings(self, left: int, right: int, upto: int) -> list[Swing]:
        """전 구간 스윙에서 그 시점까지 확정된 것만.

        스윙은 오른쪽 `right` 봉이 채워져야 확정되므로, 앞부분만 잘라 계산한 것과
        전 구간에서 걸러낸 것이 같다.
        """
        cache_key = (left, right)
        if cache_key not in self._swings:
            self._swings[cache_key] = find_swings(self.df, left, right)
        limit = upto - right
        return [s for s in self._swings[cache_key] if s.index <= limit]


@dataclass
class RuleContext:
    """규칙 하나가 볼 수 있는 전부."""

    df: pd.DataFrame                       # 확정된 봉만
    _cache: dict[tuple, pd.DataFrame] = field(default_factory=dict)
    # 백테스트처럼 봉마다 부를 때만 채워진다. 화면 한 번 계산에는 None 이다.
    source: "Precomputed | None" = None
    upto: int | None = None

    def ind(self, key: str, **params) -> pd.DataFrame:
        """지표를 계산해 준다. 같은 요청은 한 번만 계산한다."""
        if self.source is not None and self.upto is not None:
            full = self.source.frame(key, params)
            if full is not None:
                return full.iloc[: self.upto + 1]
        cache_key = (key, tuple(sorted(params.items())))
        if cache_key not in self._cache:
            self._cache[cache_key] = catalog.compute(key, self.df, params)
        return self._cache[cache_key]

    def swings(self, left: int = 5, right: int = 5) -> list[Swing]:
        if self.source is not None and self.upto is not None:
            return self.source.swings(left, right, self.upto)
        return find_swings(self.df, left, right)

    def pattern_hits(self) -> dict[str, bool]:
        """마지막 봉에서 성립한 캔들 패턴."""
        if self.source is not None and self.upto is not None:
            return {k: bool(v.iloc[self.upto]) for k, v in self.source.patterns().items()}
        return {k: bool(v.iloc[-1]) for k, v in patterns.detect(self.df).items()}

    def series(self, key: str, column: str, **params) -> pd.Series:
        return self.ind(key, **params)[column]

    @property
    def close(self) -> pd.Series:
        return self.df["close"]

    def last(self, s: pd.Series) -> float:
        """마지막 유효값. 지표가 아직 덜 데워졌으면 NaN."""
        return float(s.iloc[-1]) if len(s) and np.isfinite(s.iloc[-1]) else float("nan")

    def crossed_up(self, a: pd.Series, b: pd.Series, within: int = 1) -> bool:
        """최근 `within` 봉 안에 a 가 b 를 위로 뚫었는가."""
        cross = (a > b) & (a.shift(1) <= b.shift(1))
        return bool(cross.tail(within).any())

    def crossed_down(self, a: pd.Series, b: pd.Series, within: int = 1) -> bool:
        cross = (a < b) & (a.shift(1) >= b.shift(1))
        return bool(cross.tail(within).any())


@dataclass(frozen=True)
class RuleHit:
    key: str
    label: str
    direction: int          # +1 매수 / -1 매도
    strength: float         # 0..1 — 이 규칙이 얼마나 강하게 말하는가
    reason: str             # 화면에 그대로 나가는 한 문장
    weight: float = 1.0     # 집계할 때의 비중


Rule = Callable[[RuleContext], RuleHit | None]

_RULES: list[tuple[str, Rule]] = []


def rule(key: str) -> Callable[[Rule], Rule]:
    def wrap(fn: Rule) -> Rule:
        if any(k == key for k, _ in _RULES):
            raise RuntimeError(f"규칙 키가 겹친다: {key}")
        _RULES.append((key, fn))
        return fn

    return wrap


def all_rules() -> list[tuple[str, Rule]]:
    return list(_RULES)


def context(df: pd.DataFrame) -> RuleContext:
    return RuleContext(closed_only(df).reset_index(drop=True))


def context_at(source: Precomputed, upto: int) -> RuleContext:
    """전 구간을 미리 계산해 둔 상태에서 `upto` 번째 봉까지만 본다."""
    return RuleContext(source.df.iloc[: upto + 1], source=source, upto=upto)
