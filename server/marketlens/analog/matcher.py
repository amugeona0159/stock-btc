"""유사구간 검색.

"지금과 비슷했던 때"를 찾는다. 비슷함은 두 가지로 잰다:

- **모양** — 최근 N봉의 궤적. z-정규화해서 가격 수준과 변동성 차이를 지운다.
- **상황** — 추세·모멘텀·변동성·레짐·캘린더. 모양이 같아도 상황이 다르면 다른 사건이다.

싸게 거르고 비싸게 다시 정렬한다(유클리드로 후보 K개 → DTW 로 재정렬). DTW 를 전 구간에
돌리면 창 길이의 제곱만큼 걸려서, 5000봉짜리 히스토리에서는 화면이 멈춘다.

**미래를 보지 않게 하는 장치가 세 개 있다:**
1. 후보는 결과(이후 horizon 봉)가 이미 나온 자리만 쓴다.
2. 질의 창과 겹치는 구간은 뺀다.
3. 서로 겹치는 후보끼리도 뺀다 — 안 그러면 이웃한 거의 같은 창 스무 개가 뽑혀
   "사례 20건" 처럼 보인다. 실제로는 사례 한 건이다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..context import features as ctx

# 싸게 거를 후보 수. 이 뒤에 DTW 로 다시 정렬한다.
PRESELECT = 400
# DTW 의 밴드 폭(창 길이 대비). 좁을수록 빠르고, 시간축이 크게 밀린 것은 못 찾는다.
DTW_BAND = 0.15


@dataclass(frozen=True)
class Match:
    source: str            # 어느 계열에서 나왔나 (심볼-타임프레임)
    index: int             # 그 계열에서 창이 끝나는 봉의 위치
    ts: int                # 창이 끝나는 봉의 시각
    distance: float        # 최종 거리 (작을수록 비슷)
    shape_distance: float
    context_distance: float
    path: np.ndarray       # 이후 horizon 봉의 누적 로그수익률. path[0] = 0
    window_start_ts: int
    window_end_ts: int

    @property
    def outcome(self) -> float:
        """horizon 봉 뒤의 수익률."""
        return float(np.expm1(self.path[-1]))

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "ts": self.ts,
            "distance": round(self.distance, 4),
            "shapeDistance": round(self.shape_distance, 4),
            "contextDistance": round(self.context_distance, 4),
            "outcome": round(self.outcome, 6),
            "windowStartTs": self.window_start_ts,
            "windowEndTs": self.window_end_ts,
        }


def znorm(values: np.ndarray) -> np.ndarray:
    """z-정규화. 표준편차가 0이면(완전 횡보) 0 벡터로 둔다."""
    mean = values.mean(axis=-1, keepdims=True)
    std = values.std(axis=-1, keepdims=True)
    return np.divide(values - mean, std, out=np.zeros_like(values), where=std > 0)


def _rolling_windows(values: np.ndarray, window: int) -> np.ndarray:
    """겹치는 창을 복사 없이 만든다. (n-window+1, window) 모양."""
    return np.lib.stride_tricks.sliding_window_view(values, window)


def dtw_distance(a: np.ndarray, b: np.ndarray, band: float = DTW_BAND) -> float:
    """Sakoe-Chiba 밴드를 씌운 DTW.

    밴드가 없으면 아무 구간이나 늘려 붙여서 거리가 의미 없이 작아진다.
    """
    n, m = len(a), len(b)
    width = max(1, int(round(band * max(n, m))))
    cost = np.full((n + 1, m + 1), np.inf)
    cost[0, 0] = 0.0
    for i in range(1, n + 1):
        lo = max(1, i - width)
        hi = min(m, i + width)
        for j in range(lo, hi + 1):
            d = (a[i - 1] - b[j - 1]) ** 2
            cost[i, j] = d + min(cost[i - 1, j], cost[i, j - 1], cost[i - 1, j - 1])
    return float(np.sqrt(cost[n, m] / max(n, m)))


@dataclass
class Series:
    """검색 대상 하나. 같은 심볼의 과거일 수도, 다른 심볼일 수도 있다."""

    label: str
    df: pd.DataFrame
    context: pd.DataFrame | None = None
    # 후보로 삼을 자리(창이 끝나는 봉)를 제한한다. 시나리오가 "규제 뉴스가 있었던 때" 처럼
    # 조건을 걸면 여기에 마스크가 들어온다. None 이면 전 구간이 후보다.
    mask: np.ndarray | None = None

    def ensure_context(self) -> pd.DataFrame:
        if self.context is None:
            self.context = ctx.build(self.df)
        return self.context


def search(
    query: pd.DataFrame,
    sources: list[Series],
    window: int = 48,
    horizon: int = 24,
    top_k: int = 20,
    context_weight: float = 0.5,
    group_weights: dict[str, float] | None = None,
    use_dtw: bool = True,
    exclude_recent: int = 0,
) -> list[Match]:
    """질의 계열의 마지막 `window` 봉과 비슷했던 구간을 찾는다.

    `context_weight` 는 상황 거리의 비중이다. 0이면 모양만, 1이면 상황만 본다.
    `exclude_recent` 는 질의 계열 자신을 검색할 때 최근 몇 봉을 빼는지 — 겹침 방지용으로
    호출부가 채운다.
    """
    if len(query) < window + 5:
        return []

    query_close = query["close"].to_numpy(dtype="float64")
    query_shape = znorm(query_close[-window:])

    query_context = ctx.build(query)
    query_axes = query_context.iloc[-1].to_numpy(dtype="float64")
    axis_weights = ctx.weights(group_weights)

    matches: list[Match] = []
    for series in sources:
        matches.extend(_search_one(
            series, query_shape, query_axes, axis_weights,
            window, horizon, context_weight, use_dtw, exclude_recent,
        ))

    matches.sort(key=lambda x: x.distance)
    return _drop_overlaps(matches, window)[:top_k]


def _search_one(
    series: Series,
    query_shape: np.ndarray,
    query_axes: np.ndarray,
    axis_weights: np.ndarray,
    window: int,
    horizon: int,
    context_weight: float,
    use_dtw: bool,
    exclude_recent: int,
) -> list[Match]:
    close = series.df["close"].to_numpy(dtype="float64")
    ts = series.df["ts"].to_numpy()
    total = len(close)
    # 결과가 이미 나온 자리만 후보다. 창이 [i-window+1, i] 이고 결과는 [i+1, i+horizon].
    last_usable = total - horizon - 1 - exclude_recent
    if last_usable < window - 1:
        return []

    windows = znorm(_rolling_windows(close, window))
    ends = np.arange(window - 1, total)
    keep = ends <= last_usable
    if series.mask is not None:
        # 조건에 안 맞는 자리는 아예 후보에서 뺀다. 거리를 재고 나서 거르면
        # 상위 K개가 조건 밖 사례로 차 버려 조건부 검색이 무의미해진다.
        keep = keep & series.mask[ends]
    windows, ends = windows[keep], ends[keep]
    if len(ends) == 0:
        return []

    # 1단계: 유클리드로 싸게 거른다.
    flat = np.sqrt(((windows - query_shape) ** 2).mean(axis=1))
    order = np.argsort(flat)[:PRESELECT]

    context = series.ensure_context().to_numpy(dtype="float64")
    log_close = np.log(close)

    out: list[Match] = []
    for idx in order:
        end = int(ends[idx])
        shape = float(dtw_distance(query_shape, windows[idx])) if use_dtw else float(flat[idx])

        row = context[end]
        valid = np.isfinite(row) & np.isfinite(query_axes)
        if valid.sum() < len(query_axes) * 0.6:
            continue  # 지표가 덜 데워진 구간. 상황을 못 재면 비교 대상이 아니다.
        diff = (row[valid] - query_axes[valid]) * axis_weights[valid]
        context_distance = float(np.sqrt((diff ** 2).sum() / axis_weights[valid].sum()))

        path = log_close[end : end + horizon + 1] - log_close[end]
        out.append(Match(
            source=series.label,
            index=end,
            ts=int(ts[end]),
            distance=(1.0 - context_weight) * shape + context_weight * context_distance,
            shape_distance=shape,
            context_distance=context_distance,
            path=path,
            window_start_ts=int(ts[end - window + 1]),
            window_end_ts=int(ts[end]),
        ))
    return out


def _drop_overlaps(matches: list[Match], window: int) -> list[Match]:
    """서로 겹치는 사례를 버린다.

    이걸 안 하면 좋은 자리 하나 주변의 창 스무 개가 전부 뽑혀, 사례가 스무 건인 것처럼
    보인다. 실제로는 한 건이고, 그 위에서 계산한 확률은 전부 거짓이다.
    """
    kept: list[Match] = []
    taken: dict[str, list[int]] = {}
    gap = max(1, window // 2)
    for match in matches:
        used = taken.setdefault(match.source, [])
        if any(abs(match.index - other) < gap for other in used):
            continue
        used.append(match.index)
        kept.append(match)
    return kept
