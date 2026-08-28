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
from ...events.sources import attention
from . import market

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
# 관심도(위키백과 조회수). 문서를 못 찾는 종목이 있으므로 '있는지' 도 같이 낸다 —
# 없는 걸 NaN 으로 두면 그 종목이 학습 표에서 통째로 빠진다.
ATTENTION_COLUMNS = tuple(attention.COLUMNS) + ("attention_available",)
# 미시구조 대용. 캔들만으로 뽑을 수 있는 몇 안 되는 주문흐름 정보다 —
# 봉 안에서 종가가 어디에 붙었는지가 그 봉의 매수/매도 우위를 말해 준다.
MICRO_COLUMNS = ("clv", "signed_volume", "pressure", "wick_bias")


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

    # 후보 인덱스와 결과를 미리 준비해 둔다. 행마다 파이썬 반복을 돌면
    # 2만 봉에서 못 끝난다 — 청크 단위로 한 번에 자른다.
    columns = np.arange(m)
    known = np.isfinite(outcome)

    for start in range(0, m, CHUNK):
        stop = min(m, start + CHUNK)
        # z 는 표준화돼 있어 각 행의 제곱합이 window 다. 그래서 내적/window 가 곧 상관계수고,
        # 거리는 sqrt(2-2*corr) 로 단조 감소한다 — 상관계수만 크게 잡으면 된다.
        corr = (z[start:stop] @ z.T) / float(window)

        # 행 q 가 쓸 수 있는 후보는 j <= q-gap 이면서 결과를 아는 자리뿐이다.
        limits = np.arange(start, stop)[:, None] - gap
        usable = (columns[None, :] <= limits) & known[None, :]
        if not usable.any():
            continue
        # 못 쓰는 자리는 -inf 로 눌러 argpartition 이 절대 안 고르게 한다.
        scores = np.where(usable, corr, -np.inf)
        enough = usable.sum(axis=1) >= k
        if not enough.any():
            continue

        top = np.argpartition(-scores, k - 1, axis=1)[:, :k]
        rows = np.arange(stop - start)[:, None]
        picked = outcome[top]
        picked_corr = scores[rows, top]

        bars = np.arange(start, stop) + window - 1
        for column, values in (
            (0, np.median(picked, axis=1)),
            (1, picked.mean(axis=1)),
            (2, (picked > 0).mean(axis=1)),
            (3, picked.std(axis=1, ddof=1) if k > 1 else np.zeros(stop - start)),
            (4, picked_corr.max(axis=1)),
            (5, picked_corr.mean(axis=1)),
        ):
            out.iloc[bars[enough], column] = values[enough]

    # 수익률 계열은 tanh 로 눌러 다른 축과 크기를 맞춘다.
    for column in ("analog_median", "analog_mean", "analog_spread"):
        out[column] = np.tanh(out[column] / 0.05)
    out["analog_prob_up"] = out["analog_prob_up"] * 2.0 - 1.0
    return out


def micro_features(df: pd.DataFrame) -> pd.DataFrame:
    """봉 안의 매수/매도 압력 대용.

    체결 단위 주문흐름은 캔들에 없다. 대신 종가가 봉의 어디에 붙었는지(CLV)와
    그걸 거래량으로 가중한 값을 쓴다 — 있는 것 중에서는 이게 제일 가깝다.
    """
    out = pd.DataFrame(index=df.index, dtype="float64")
    high, low, close, open_ = df["high"], df["low"], df["close"], df["open"]
    span = (high - low).replace(0.0, np.nan)

    # -1(저가 마감) ~ +1(고가 마감)
    clv = ((close - low) - (high - close)) / span
    out["clv"] = clv.fillna(0.0)

    volume = df["volume"].astype("float64")
    average = volume.rolling(20, min_periods=5).mean().replace(0.0, np.nan)
    out["signed_volume"] = np.tanh(clv.fillna(0.0) * (volume / average))
    # 최근 압력의 누적. 한 봉의 CLV 는 잡음이 크다.
    out["pressure"] = np.tanh(out["signed_volume"].rolling(10, min_periods=3).mean() * 3.0)

    # 위꼬리와 아래꼬리 중 어느 쪽이 긴가. 되돌림의 방향을 말해 준다.
    body_top = close.combine(open_, max)
    body_bottom = close.combine(open_, min)
    out["wick_bias"] = (((body_bottom - low) - (high - body_top)) / span).fillna(0.0)
    return out.replace([np.inf, -np.inf], np.nan)


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


def attention_columns(df: pd.DataFrame, frame: pd.DataFrame | None) -> pd.DataFrame:
    """관심도 축을 학습 표에 넣을 수 있는 형태로.

    자료가 없으면 0(중립)으로 채우고 `attention_available` 을 -1 로 둔다. NaN 으로
    두면 그 행이 통째로 빠져, 위키백과 문서가 없는 종목은 학습에서 사라진다.
    """
    out = pd.DataFrame(index=df.index, dtype="float64")
    available = frame is not None and not frame.empty and frame["attention_z"].notna().any()
    for column in attention.COLUMNS:
        values = frame[column] if available and column in frame else pd.Series(np.nan, index=df.index)
        out[column] = values.reindex(df.index).fillna(0.0)
    out["attention_available"] = 1.0 if available else -1.0
    return out


def build(
    df: pd.DataFrame,
    events: list[Event] | None = None,
    window: int = 48,
    horizon: int = 24,
    neighbours: int = NEIGHBOURS,
    attention_frame: pd.DataFrame | None = None,
    market_frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """상황 + 유사구간 + 이벤트 + 관심도 + 미시구조 + 시장요인을 한 표로.

    `market_frame` 은 여러 종목이 있어야 만들어지므로 밖에서 넣어 준다
    (`market.market_series` → `market.features`).
    """
    closed = closed_only(df).reset_index(drop=True)
    if len(closed) < window + horizon + 60:
        return pd.DataFrame()

    frame = ctx.build(closed)
    frame = frame.join(analog_features(closed, window, horizon, neighbours))
    frame = frame.join(event_features(closed, events or []))
    frame = frame.join(attention_columns(closed, attention_frame))
    frame = frame.join(micro_features(closed))
    if market_frame is not None and not market_frame.empty:
        for column in market.COLUMNS:
            frame[column] = market_frame.get(column, np.nan).to_numpy()
    else:
        # 시장 요인은 여러 종목이 있어야 만들어진다. 없으면 0(중립)으로 두되
        # 없다는 사실을 축 하나로 남긴다 — NaN 으로 두면 그 종목이 학습에서 빠진다.
        for column in market.COLUMNS:
            frame[column] = 0.0
    frame["market_available"] = 1.0 if (market_frame is not None
                                        and not market_frame.empty) else -1.0
    frame.insert(0, "ts", closed["ts"])
    return frame.replace([np.inf, -np.inf], np.nan)


MARKET_COLUMNS: tuple[str, ...] = tuple(market.COLUMNS) + ("market_available",)

FEATURE_COLUMNS: tuple[str, ...] = (
    tuple(ctx.AXES) + ANALOG_COLUMNS + EVENT_COLUMNS + ATTENTION_COLUMNS
    + MICRO_COLUMNS + MARKET_COLUMNS
)
