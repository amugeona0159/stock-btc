"""거래량 지표."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..core.registry import IndicatorSpec, Output, Param, indicator
from . import _math as m


@indicator(IndicatorSpec(
    key="obv",
    name="OBV",
    category="volume",
    pane="own",
    formula="종가가 오른 봉은 거래량을 더하고 내린 봉은 뺀 누적합",
    outputs=(Output("value", "OBV", pane="own", color="accent"),),
    warmup=lambda p: 2,
    source="Joseph Granville (1963)",
))
def _obv(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    direction = np.sign(df["close"].diff().fillna(0.0))
    return pd.DataFrame({"value": (direction * df["volume"]).cumsum()})


@indicator(IndicatorSpec(
    key="vwap",
    name="VWAP",
    category="volume",
    formula="구간 누적(hlc3 · 거래량) / 구간 누적 거래량. 구간이 바뀌면 0에서 다시 쌓는다.",
    params=(Param("anchor", "기준", "day", kind="choice",
                  choices=("day", "week", "session", "all")),),
    outputs=(Output("value", "VWAP", color="accent"),),
    warmup=lambda p: 1,
))
def _vwap(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    # 봉 시각(UTC ms)만으로 구간을 가른다. 거래소 개장 시각은 프로바이더가 아니라
    # 여기 알 바가 아니다 - 필요하면 anchor 를 늘리는 쪽으로 간다.
    ts = df["ts"].to_numpy()
    if p["anchor"] == "all":
        group = np.zeros(len(df), dtype="int64")
    elif p["anchor"] == "week":
        group = (ts // 604_800_000).astype("int64")
    else:  # day / session
        group = (ts // 86_400_000).astype("int64")

    tp = m.hlc3(df)
    flow = (tp * df["volume"]).groupby(group).cumsum()
    vol = df["volume"].groupby(group).cumsum()
    return pd.DataFrame({"value": flow / vol.replace(0.0, np.nan)})


@indicator(IndicatorSpec(
    key="ad",
    name="누적분포선 A/D",
    category="volume",
    pane="own",
    formula="자금승수 = ((종가-저) - (고-종가)) / (고-저) · A/D = 누적(자금승수 · 거래량)",
    outputs=(Output("value", "A/D", pane="own", color="accent"),),
    warmup=lambda p: 2,
))
def _ad(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    span = (df["high"] - df["low"]).replace(0.0, np.nan)
    multiplier = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / span
    return pd.DataFrame({"value": (multiplier.fillna(0.0) * df["volume"]).cumsum()})


@indicator(IndicatorSpec(
    key="cmf",
    name="차이킨 자금흐름 CMF",
    category="volume",
    pane="own",
    formula="CMF = n봉 합(자금승수·거래량) / n봉 합(거래량)",
    params=(Param("period", "기간", 20, min=2, max=500),),
    outputs=(Output("value", "CMF", pane="own", color="accent"),),
    warmup=lambda p: p["period"],
))
def _cmf(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    n = p["period"]
    span = (df["high"] - df["low"]).replace(0.0, np.nan)
    multiplier = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / span
    flow = (multiplier.fillna(0.0) * df["volume"]).rolling(n, min_periods=n).sum()
    vol = df["volume"].rolling(n, min_periods=n).sum()
    return pd.DataFrame({"value": flow / vol.replace(0.0, np.nan)})


@indicator(IndicatorSpec(
    key="volume_profile",
    name="거래량 프로파일",
    category="volume",
    formula="가격을 칸으로 나눠 각 칸에 거래량을 쌓고, 가장 두꺼운 칸이 POC. "
            "POC 에서 양옆으로 총 거래량의 지정 비율만큼 넓힌 구간이 밸류에어리어.",
    params=(
        Param("bins", "가격 칸 수", 60, min=5, max=500),
        Param("lookback", "구간 봉 수", 0, min=0, max=100000),
        Param("value_area", "밸류에어리어 비율", 0.7, kind="float", min=0.1, max=0.99, step=0.01),
    ),
    outputs=(
        Output("poc", "POC", draw="level", color="warn"),
        Output("vah", "VAH", draw="level", color="neutral"),
        Output("val", "VAL", draw="level", color="neutral"),
    ),
    warmup=lambda p: max(2, p["lookback"]),
))
def _volume_profile(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    # 시간축 지표가 아니라 수평선이다. 값을 전 구간에 같게 깔아 화면이 선으로 긋게 한다.
    out = pd.DataFrame(
        {"poc": np.nan, "vah": np.nan, "val": np.nan}, index=df.index, dtype="float64"
    )
    window = df.tail(p["lookback"]) if p["lookback"] else df
    if len(window) < 2 or window["volume"].sum() <= 0:
        return out

    low, high = float(window["low"].min()), float(window["high"].max())
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return out

    edges = np.linspace(low, high, p["bins"] + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    # 봉 하나의 거래량은 그 봉의 고-저 범위에 고르게 흩는다. 종가 한 점에 몰면
    # 긴 꼬리를 가진 봉이 실제로 거래된 가격대를 통째로 놓친다.
    weights = np.zeros(p["bins"], dtype="float64")
    for lo, hi, vol in zip(window["low"], window["high"], window["volume"]):
        if vol <= 0:
            continue
        overlap = np.clip(np.minimum(edges[1:], hi) - np.maximum(edges[:-1], lo), 0.0, None)
        total = overlap.sum()
        weights += vol * (overlap / total) if total > 0 else 0.0

    if weights.sum() <= 0:
        return out
    poc_idx = int(weights.argmax())
    target = p["value_area"] * weights.sum()
    lo_idx = hi_idx = poc_idx
    covered = weights[poc_idx]
    while covered < target and (lo_idx > 0 or hi_idx < p["bins"] - 1):
        below = weights[lo_idx - 1] if lo_idx > 0 else -1.0
        above = weights[hi_idx + 1] if hi_idx < p["bins"] - 1 else -1.0
        if above >= below:
            hi_idx += 1
            covered += weights[hi_idx]
        else:
            lo_idx -= 1
            covered += weights[lo_idx]

    out["poc"] = centers[poc_idx]
    out["vah"] = edges[hi_idx + 1]
    out["val"] = edges[lo_idx]
    return out
