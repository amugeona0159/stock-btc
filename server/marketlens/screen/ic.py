"""랭크 IC — 팩터가 실제로 종목을 줄 세우는지 재는 곳.

**IC(정보계수)**: 같은 시각에 종목들을 팩터로 줄 세운 순위와, 그 뒤 실제로 간
수익률의 순위가 얼마나 같은 방향인가(스피어만 상관). 시각마다 하나씩 나오고,
그 평균이 이 팩터의 성적이다.

왜 상관이 아니라 **랭크** 상관인가: 종목마다 변동성이 달라 원값을 그대로 비교하면
BTC 한 종목이 순위를 결정해 버린다. 순위로 바꾸면 그게 사라진다.

## 크기를 미리 알아 둘 것

주식 팩터의 IC 는 **0.02~0.05 면 쓸 만한 축**이다. 0.3 같은 값이 나오면 기뻐할 게
아니라 미래가 새고 있는지 의심해야 한다. 여기서 재는 값도 대개 0.0x 대다.

## 부호는 재서 정한다

"RSI 가 낮으면 산다" 를 미리 적지 않는다. 폴드마다 IC 부호가 **일관될 때만** 그
부호를 쓰고, 폴드에 따라 부호가 뒤집히는 축은 버린다. 평균만 보면 한 폴드에서 크게
이긴 축이 나머지에서 다 져도 살아남는다.

## 두 갈래

- `direction` — 팩터 순위 vs **수익률** 순위
- `move` — 팩터 순위 vs **|수익률|** 순위 ("얼마나 움직일까")

"관심있게 볼 종목"은 대개 뒤쪽이다. 방향을 못 맞혀도 크게 움직일 종목은 볼 값어치가 있다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..forecast.ml.model import time_folds
from .universe import MIN_BREADTH

# 폴드 평균 |IC| 가 이보다 작으면 점수에 안 넣는다. 주식 팩터의 현실적인 하한이다.
MIN_IC = 0.01
# 부호가 이 비율 이상 같은 방향이어야 쓴다. 폴드가 4개면 4개 다 같아야 한다.
SIGN_AGREEMENT = 1.0
# 폴드가 이보다 적으면 '부호가 일관되다'는 말이 성립하지 않는다. 둘 중 둘이 같은
# 방향일 확률은 잡음이어도 절반이라, 2폴드는 사실상 아무것도 안 거른다.
MIN_FOLDS = 3
# 한 시각에 이만큼은 있어야 순위가 의미 있다.
MIN_SYMBOLS = MIN_BREADTH


@dataclass
class FactorScore:
    """팩터 하나의 성적. 그대로 `learning/factors.json` 에 들어간다."""

    factor: str
    kind: str                      # "direction" | "move"
    ic: float                      # 폴드 평균
    fold_ic: list[float] = field(default_factory=list)
    t_stat: float = 0.0
    coverage: int = 0              # IC 를 잰 시각의 수
    usable: bool = False
    reason: str = ""


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    """순위 상관. scipy 없이 — 순위로 바꾼 뒤 피어슨이 곧 스피어만이다."""
    if len(a) < MIN_SYMBOLS:
        return np.nan
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    sa, sb = ra.std(), rb.std()
    if sa == 0 or sb == 0:                 # 전부 같은 값이면 순위가 없다
        return np.nan
    return float(((ra - ra.mean()) * (rb - rb.mean())).mean() / (sa * sb))


def per_bar_ic(panel: pd.DataFrame, factor: str, target: str) -> pd.Series:
    """시각마다의 IC. 인덱스는 ts.

    `panel` 은 롱 포맷 — 한 행이 (ts, symbol, 팩터들…, 라벨).
    """
    if factor not in panel.columns or target not in panel.columns:
        return pd.Series(dtype="float64")
    rows = panel[["ts", factor, target]].dropna()
    if rows.empty:
        return pd.Series(dtype="float64")
    out = rows.groupby("ts").apply(
        lambda g: _spearman(g[factor].to_numpy(), g[target].to_numpy()),
        include_groups=False,
    )
    return out.dropna()


def measure(panel: pd.DataFrame, factor: str, target: str, kind: str,
            horizon_ms: int, folds: int = 4) -> FactorScore:
    """폴드로 나눠 IC 를 잰다.

    폴드를 나누는 이유는 학습이 아니라 **부호의 안정성**을 보기 위해서다. 전 구간
    평균 IC 하나만 보면, 한 시기에만 통했던 축과 늘 통한 축을 구별할 수 없다.
    """
    series = per_bar_ic(panel, factor, target)
    score = FactorScore(factor=factor, kind=kind, ic=0.0, coverage=len(series))
    if len(series) < 40:
        score.reason = f"IC 를 잰 시각이 {len(series)}개뿐이다"
        return score

    ts = series.index.to_numpy()
    values = series.to_numpy(dtype="float64")
    # `time_folds` 는 (학습, 검증) 을 준다. 여기서는 학습이 없으므로 검증 칸만 쓴다 —
    # 구간 나누기와 퍼지 규칙을 그대로 물려받으려고 같은 함수를 쓴다.
    split = time_folds(ts, horizon_ms, folds)
    chunks = [values[test] for _, test in split if test.sum() >= 10]
    fold_ic = [float(np.mean(c)) for c in chunks]
    if len(chunks) < MIN_FOLDS:
        # 부호 안정성을 확인할 수 없으면 쓰지 않는다. 평균은 적어 두되 usable 은 아니다.
        score.ic = float(np.mean(values))
        score.fold_ic = [round(v, 5) for v in fold_ic]
        score.reason = (f"폴드가 {len(chunks)}개뿐이라 부호 안정성을 못 본다 "
                        f"(최소 {MIN_FOLDS}개)")
        return score

    mean = float(np.mean(fold_ic))
    # t 값은 시각별 IC 의 흩어짐으로 낸다(폴드 4개로 t 를 내면 자유도가 3이다).
    spread = float(np.std(values, ddof=1))
    t_stat = mean * np.sqrt(len(values)) / spread if spread > 0 else 0.0

    agree = max((np.array(fold_ic) > 0).mean(), (np.array(fold_ic) < 0).mean())
    score.ic = mean
    score.fold_ic = [round(v, 5) for v in fold_ic]
    score.t_stat = round(float(t_stat), 3)
    if abs(mean) < MIN_IC:
        score.reason = f"평균 |IC| {abs(mean):.4f} < {MIN_IC}"
    elif agree < SIGN_AGREEMENT:
        score.reason = f"폴드마다 부호가 뒤집힌다 ({fold_ic})"
    else:
        score.usable = True
        score.reason = f"폴드 {len(fold_ic)}개 모두 같은 방향"
    return score


def measure_all(panel: pd.DataFrame, candidates, horizon_ms: int,
                folds: int = 4) -> dict[str, list[FactorScore]]:
    """`direction` 과 `move` 두 갈래로 전부 잰다."""
    out: dict[str, list[FactorScore]] = {}
    for kind, target in (("direction", "fwd"), ("move", "fwd_abs")):
        out[kind] = [measure(panel, f, target, kind, horizon_ms, folds)
                     for f in candidates if f in panel.columns]
        out[kind].sort(key=lambda s: -abs(s.ic))
    return out
