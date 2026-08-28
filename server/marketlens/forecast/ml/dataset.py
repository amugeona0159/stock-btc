"""학습용 표 만들기.

핵심은 여기다. 유사구간과 이벤트를 **답으로 쓰지 않고 피처로 넣는다.**

검색 기반은 "비슷한 과거 20건의 중앙값" 을 그대로 답이라고 내놓는다. 그러면
사례가 잘 맞는 상황과 안 맞는 상황을 구분하지 못한다. 대신 그 20건의 요약
(중앙값·상승비중·흩어진 정도·얼마나 비슷한지)을 **입력**으로 주고, 그게 실제로
얼마나 맞았는지를 학습시키면 모델이 "지금은 사례를 믿을 자리인가" 까지 배운다.

검색 결과를 예측 모형의 입력으로 쓰는 방식은 시계열 예측에서 검색 증강(retrieval
augmented)이라 부른다 — `research.library: analog_retrieval_forecast`.

**속도**: 봉마다 검색을 다시 돌리면 학습 표를 못 만든다(수천 봉 × DTW).
z-정규화한 창끼리는 거리 계산이 상관계수 하나로 줄어들어(‖z₁−z₂‖² = 2n(1−corr)),
행렬 곱 한 번이면 전부 나온다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ...analog.matcher import znorm
from ...context import features as ctx
from ...core.candle import closed_only  # noqa: F401  (model.py 가 여기서 가져다 쓴다)
from ...events.schema import Event

# 한 번에 계산할 질의 수. 전체를 한 행렬로 만들면 5000봉에서 200MB 를 넘긴다.
CHUNK = 512
# 사례에서 뽑는 이웃 수. 너무 적으면 잡음, 너무 많으면 전부 평균으로 뭉갠다.
NEIGHBOURS = 20

ANALOG_COLUMNS = (
    "analog_median", "analog_mean", "analog_prob_up",
    "analog_spread", "analog_corr_max", "analog_corr_mean",
)
EVENT_COLUMNS = (
    "event_recency", "event_severity", "event_chart", "event_macro", "event_scheduled",
)


def analog_features(
    df: pd.DataFrame, window: int = 48, horizon: int = 24, k: int = NEIGHBOURS
) -> pd.DataFrame:
    """봉마다 '그 시점에서 검색했을 때 나왔을 사례들'의 요약.

    각 봉에서 쓰는 후보는 **결과가 이미 나온** 창뿐이다. 이걸 어기면 학습 표가
    미래를 담게 되고, 그 위에서 잰 성능은 전부 거짓이 된다.
    """
    out = pd.DataFrame(
        {c: np.nan for c in ANALOG_COLUMNS}, index=df.index, dtype="float64"
    )
    close = df["close"].to_numpy(dtype="float64")
    n = len(close)
    if n < window + horizon + k + 10:
        return out

    # 행 j 는 인덱스 j+window-1 에서 끝나는 창이다.
    z = znorm(np.lib.stride_tricks.sliding_window_view(close, window)).astype("float32")
    m = len(z)
    log_close = np.log(close)

    # 창 끝 e 에서 horizon 봉 뒤까지의 로그수익률. 결과를 모르는 자리는 NaN.
    ends = np.arange(window - 1, n)
    outcome = np.full(m, np.nan)
    known = ends + horizon < n
    outcome[known] = log_close[ends[known] + horizon] - log_close[ends[known]]

    # 질의 창과 겹치는 후보는 뺀다. 겹친 창은 거의 같은 그림이라 '사례'가 아니다.
    gap = max(horizon, window // 2)

    for start in range(0, m, CHUNK):
        stop = min(m, start + CHUNK)
        # z 는 표준화돼 있어 각 행의 제곱합이 window 다. 그래서 내적/window 가 곧 상관계수고,
        # 거리는 sqrt(2-2*corr) 로 단조 감소한다 — 상관계수만 크게 잡으면 된다.
        corr = (z[start:stop] @ z.T) / float(window)

        for row in range(stop - start):
            q = start + row
            limit = q - gap
            if limit < k:
                continue
            candidates = corr[row, : limit + 1]
            values = outcome[: limit + 1]
            usable = np.isfinite(values) & np.isfinite(candidates)
            if usable.sum() < k:
                continue

            index = np.flatnonzero(usable)
            scores = candidates[index]
            top = index[np.argpartition(-scores, k - 1)[:k]]
            picked = values[top]
            picked_corr = candidates[top]

            bar = q + window - 1
            out.iloc[bar, 0] = float(np.median(picked))
            out.iloc[bar, 1] = float(picked.mean())
            out.iloc[bar, 2] = float((picked > 0).mean())
            out.iloc[bar, 3] = float(picked.std(ddof=1)) if k > 1 else 0.0
            out.iloc[bar, 4] = float(picked_corr.max())
            out.iloc[bar, 5] = float(picked_corr.mean())

    # 수익률 계열은 tanh 로 눌러 다른 축과 크기를 맞춘다.
    for column in ("analog_median", "analog_mean", "analog_spread"):
        out[column] = np.tanh(out[column] / 0.05)
    out["analog_prob_up"] = out["analog_prob_up"] * 2.0 - 1.0
    return out


def event_features(df: pd.DataFrame, events: list[Event]) -> pd.DataFrame:
    """봉마다 '최근에 무슨 일이 있었나'.

    앞으로 일어날 사건은 쓰지 않는다. 예정된 일정은 실제로 미리 알 수 있지만,
    그걸 쓰기 시작하면 어느 사건이 사전에 알려진 것인지 일일이 따져야 한다 —
    학습 표에서 미래가 새는 제일 흔한 경로다.
    """
    out = pd.DataFrame(
        {c: 0.0 for c in EVENT_COLUMNS}, index=df.index, dtype="float64"
    )
    if not events:
        out["event_recency"] = -1.0
        return out

    ts = df["ts"].to_numpy()
    n = len(ts)
    positions = np.searchsorted(ts, [e.ts for e in events], side="left")

    last_at = np.full(n, -1, dtype="int64")
    severity = np.zeros(n)
    by_kind = {"chart": np.zeros(n), "macro": np.zeros(n)}
    scheduled = np.zeros(n)

    for event, position in zip(events, positions):
        if position >= n:
            continue
        last_at[position] = position
        severity[position] = max(severity[position], event.severity)
        bucket = by_kind.get("chart" if event.kind == "chart" else "macro")
        if bucket is not None:
            bucket[position] = max(bucket[position], event.severity)
        if event.scheduled:
            scheduled[position] = 1.0

    # 마지막 사건 이후 몇 봉인가. 최근일수록 1에 가깝다.
    latest = -1
    recency = np.zeros(n)
    for i in range(n):
        if last_at[i] >= 0:
            latest = i
        recency[i] = -1.0 if latest < 0 else float(np.exp(-(i - latest) / 20.0)) * 2.0 - 1.0

    def decayed(values: np.ndarray, span: int = 20) -> np.ndarray:
        # 사건의 영향이 봉이 지날수록 옅어진다고 본다. 창 안의 최댓값보다 부드럽다.
        return pd.Series(values).ewm(span=span, adjust=False).mean().to_numpy()

    out["event_recency"] = recency
    out["event_severity"] = decayed(severity)
    out["event_chart"] = decayed(by_kind["chart"])
    out["event_macro"] = decayed(by_kind["macro"])
    out["event_scheduled"] = decayed(scheduled)
    return out


def forward_return(df: pd.DataFrame, horizon: int) -> pd.Series:
    """h봉 뒤까지의 로그수익률. 마지막 h봉은 답을 모르므로 NaN 이다."""
    log_close = np.log(df["close"].astype("float64"))
    return log_close.shift(-horizon) - log_close


def build(
    df: pd.DataFrame,
    events: list[Event] | None = None,
    window: int = 48,
    horizon: int = 24,
    neighbours: int = NEIGHBOURS,
) -> pd.DataFrame:
    """상황 + 유사구간 + 이벤트를 한 표로. 라벨은 붙이지 않는다."""
    closed = closed_only(df).reset_index(drop=True)
    if len(closed) < window + horizon + 60:
        return pd.DataFrame()

    frame = ctx.build(closed)
    frame = frame.join(analog_features(closed, window, horizon, neighbours))
    frame = frame.join(event_features(closed, events or []))
    frame.insert(0, "ts", closed["ts"])
    return frame.replace([np.inf, -np.inf], np.nan)


FEATURE_COLUMNS: tuple[str, ...] = tuple(ctx.AXES) + ANALOG_COLUMNS + EVENT_COLUMNS
