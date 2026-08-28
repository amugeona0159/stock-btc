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
from ..indicators import catalog


@dataclass
class RuleContext:
    """규칙 하나가 볼 수 있는 전부."""

    df: pd.DataFrame                       # 확정된 봉만
    _cache: dict[tuple, pd.DataFrame] = field(default_factory=dict)

    def ind(self, key: str, **params) -> pd.DataFrame:
        """지표를 계산해 준다. 같은 요청은 한 번만 계산한다."""
        cache_key = (key, tuple(sorted(params.items())))
        if cache_key not in self._cache:
            self._cache[cache_key] = catalog.compute(key, self.df, params)
        return self._cache[cache_key]

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
