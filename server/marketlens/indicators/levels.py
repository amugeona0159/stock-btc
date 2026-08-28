"""가격 레벨 - 피보나치, 피벗, 지지·저항 군집.

전부 수평선이다. 값이 전 구간에 같게 깔린 시리즈로 나가고, 화면이 그걸 선으로 긋는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..core.registry import IndicatorSpec, Output, Param, indicator
from .structure import find_swings, last_leg

# 되돌림 비율. 0.5 는 피보나치 수열에서 나오지 않지만 관례로 늘 함께 쓴다.
RETRACEMENTS = (0.236, 0.382, 0.5, 0.618, 0.786)
EXTENSIONS = (1.272, 1.618, 2.618)

_RETRACEMENT_OUTPUTS = tuple(
    Output(f"r{int(r * 1000):04d}", f"{r:.3f}", draw="level",
           color="warn" if r in (0.5, 0.618) else "neutral")
    for r in RETRACEMENTS
)
_EXTENSION_OUTPUTS = tuple(
    Output(f"e{int(e * 1000):04d}", f"{e:.3f}", draw="level", color="accent", optional=True)
    for e in EXTENSIONS
)


@indicator(IndicatorSpec(
    key="fibonacci",
    name="피보나치 되돌림",
    category="level",
    formula="최근 확정된 스윙 한 다리(고->저 또는 저->고)를 잡아 "
            "레벨 = 끝점 - 비율·(끝점 - 시작점). 0.618 은 황금비의 역수, "
            "0.382 = 1 - 0.618, 0.236 = 0.618^3. 확장은 다리 밖으로 1.272·1.618·2.618.",
    params=(
        Param("left", "스윙 왼쪽 봉", 5, min=1, max=100),
        Param("right", "스윙 오른쪽 봉", 5, min=1, max=100),
    ),
    outputs=(
        Output("start", "다리 시작", draw="level", color="neutral", optional=True),
        Output("end", "다리 끝", draw="level", color="neutral", optional=True),
        *_RETRACEMENT_OUTPUTS,
        *_EXTENSION_OUTPUTS,
    ),
    warmup=lambda p: (p["left"] + p["right"] + 1) * 3,
))
def _fibonacci(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    keys = ["start", "end"] + [o.key for o in _RETRACEMENT_OUTPUTS + _EXTENSION_OUTPUTS]
    out = pd.DataFrame({k: np.nan for k in keys}, index=df.index, dtype="float64")

    leg = last_leg(find_swings(df, p["left"], p["right"]))
    if leg is None:
        return out
    start, end = leg
    span = end.price - start.price
    if span == 0:
        return out

    # 다리가 생기기 전 구간에는 긋지 않는다. 전 구간에 깔면 아직 존재하지도 않던
    # 되돌림 선이 옛날 봉 위에 그어져, 그때 지지받은 것처럼 보인다.
    # find_swings 는 위치 인덱스를 준다. 라벨 인덱스와 섞지 않는다.
    live = np.arange(len(out)) >= start.index

    out.loc[live, "start"] = start.price
    out.loc[live, "end"] = end.price
    for ratio, spec in zip(RETRACEMENTS, _RETRACEMENT_OUTPUTS):
        out.loc[live, spec.key] = end.price - ratio * span
    for ratio, spec in zip(EXTENSIONS, _EXTENSION_OUTPUTS):
        # 확장은 다리가 간 방향으로 더 나간다 - 되돌림과 부호가 반대다.
        out.loc[live, spec.key] = start.price + ratio * span
    return out


def _previous_period_hlc(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """직전 구간의 고·저·종가를 각 봉에 붙인다.

    구간은 봉 간격에서 정한다 - 일봉 이상이면 주 단위, 그보다 짧으면 일 단위.
    피벗은 원래 '어제 값으로 오늘을 가른다'는 지표라 구간을 잘못 잡으면 의미가 없다.
    """
    ts = df["ts"].to_numpy()
    step = float(np.median(np.diff(ts))) if len(ts) > 1 else 86_400_000.0
    bucket = 604_800_000 if step >= 86_400_000 else 86_400_000
    group = pd.Series((ts // bucket).astype("int64"), index=df.index)

    agg = df.groupby(group).agg(high=("high", "max"), low=("low", "min"), close=("close", "last"))
    prev = agg.shift(1)
    mapped = prev.reindex(group.to_numpy())
    mapped.index = df.index
    return mapped["high"], mapped["low"], mapped["close"]


_PIVOT_OUTPUTS = (
    Output("p", "피벗", draw="level", color="warn"),
    Output("r1", "R1", draw="level", color="down"),
    Output("r2", "R2", draw="level", color="down"),
    Output("r3", "R3", draw="level", color="down", optional=True),
    Output("s1", "S1", draw="level", color="up"),
    Output("s2", "S2", draw="level", color="up"),
    Output("s3", "S3", draw="level", color="up", optional=True),
)


@indicator(IndicatorSpec(
    key="pivots",
    name="피벗 포인트",
    category="level",
    formula="classic: P=(H+L+C)/3, R1=2P-L, S1=2P-H, R2=P+(H-L), S2=P-(H-L) · "
            "fibonacci: R/S = P ± 0.382·0.618·1.0 ·(H-L) · "
            "camarilla: C ± (H-L)·1.1/12, /6, /4 · woodie: P=(H+L+2C)/4",
    params=(Param("method", "방식", "classic", kind="choice",
                  choices=("classic", "fibonacci", "camarilla", "woodie")),),
    outputs=_PIVOT_OUTPUTS,
    warmup=lambda p: 2,
))
def _pivots(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    high, low, close = _previous_period_hlc(df)
    span = high - low
    method = p["method"]

    if method == "woodie":
        pivot = (high + low + 2.0 * close) / 4.0
    elif method == "camarilla":
        pivot = (high + low + close) / 3.0
    else:
        pivot = (high + low + close) / 3.0

    if method == "fibonacci":
        r1, r2, r3 = pivot + 0.382 * span, pivot + 0.618 * span, pivot + span
        s1, s2, s3 = pivot - 0.382 * span, pivot - 0.618 * span, pivot - span
    elif method == "camarilla":
        r1, r2, r3 = close + span * 1.1 / 12, close + span * 1.1 / 6, close + span * 1.1 / 4
        s1, s2, s3 = close - span * 1.1 / 12, close - span * 1.1 / 6, close - span * 1.1 / 4
    else:  # classic / woodie 는 P 만 다르고 나머지 식은 같다
        r1, s1 = 2.0 * pivot - low, 2.0 * pivot - high
        r2, s2 = pivot + span, pivot - span
        r3, s3 = high + 2.0 * (pivot - low), low - 2.0 * (high - pivot)

    return pd.DataFrame(
        {"p": pivot, "r1": r1, "r2": r2, "r3": r3, "s1": s1, "s2": s2, "s3": s3},
        index=df.index,
    )


_SR_SLOTS = 6
_SR_OUTPUTS = tuple(
    Output(f"level{i + 1}", f"레벨{i + 1}", draw="level",
           color="neutral", optional=i >= 3)
    for i in range(_SR_SLOTS)
)


@indicator(IndicatorSpec(
    key="support_resistance",
    name="지지·저항",
    category="level",
    formula="스윙 고·저를 모아 서로 허용 오차 안에 있는 것끼리 묶고, "
            "많이 겹친 군집부터 대표값(평균)을 수평선으로 낸다. 여러 번 부딪힌 자리일수록 앞에 온다.",
    params=(
        Param("left", "스윙 왼쪽 봉", 5, min=1, max=100),
        Param("right", "스윙 오른쪽 봉", 5, min=1, max=100),
        Param("tolerance", "묶는 오차(%)", 0.5, kind="float", min=0.01, max=10.0, step=0.01),
    ),
    outputs=_SR_OUTPUTS,
    warmup=lambda p: (p["left"] + p["right"] + 1) * 5,
))
def _support_resistance(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    out = pd.DataFrame(
        {o.key: np.nan for o in _SR_OUTPUTS}, index=df.index, dtype="float64"
    )
    prices = sorted(s.price for s in find_swings(df, p["left"], p["right"]))
    if not prices:
        return out

    tolerance = p["tolerance"] / 100.0
    clusters: list[list[float]] = [[prices[0]]]
    for price in prices[1:]:
        anchor = clusters[-1][0]
        if anchor > 0 and abs(price - anchor) / anchor <= tolerance:
            clusters[-1].append(price)
        else:
            clusters.append([price])

    # 많이 부딪힌 순, 같으면 최근 가격에 가까운 순.
    last_price = float(df["close"].iloc[-1])
    ranked = sorted(
        clusters,
        key=lambda c: (-len(c), abs(float(np.mean(c)) - last_price)),
    )
    for slot, cluster in zip(_SR_OUTPUTS, ranked[:_SR_SLOTS]):
        out[slot.key] = float(np.mean(cluster))
    return out
