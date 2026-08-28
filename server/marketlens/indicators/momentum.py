"""모멘텀 지표."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..core.registry import IndicatorSpec, Output, Param, indicator
from . import _math as m
from .trend import SOURCES


def _rsi(src: pd.Series, period: int) -> pd.Series:
    delta = src.diff()
    gain = m.rma(delta.clip(lower=0.0), period)
    loss = m.rma((-delta).clip(lower=0.0), period)
    # 손실이 0인 구간은 RS 가 무한대 - 100 으로 못박는다. NaN 으로 두면 상승장에서 선이 끊긴다.
    rs = gain / loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.where(loss.ne(0.0) | gain.isna(), 100.0)


@indicator(IndicatorSpec(
    key="rsi",
    name="RSI",
    category="momentum",
    pane="own",
    formula="RS = RMA(상승폭, n) / RMA(하락폭, n) · RSI = 100 - 100/(1+RS)",
    params=(Param("period", "기간", 14, min=2, max=500),
            Param("source", "가격", "close", kind="choice", choices=SOURCES)),
    outputs=(Output("value", "RSI", pane="own", color="accent"),),
    warmup=lambda p: p["period"] * 3,
    source="J. Welles Wilder, New Concepts in Technical Trading Systems (1978)",
))
def _rsi_ind(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    return pd.DataFrame({"value": _rsi(m.source(df, p["source"]), p["period"])})


@indicator(IndicatorSpec(
    key="stoch",
    name="스토캐스틱",
    category="momentum",
    pane="own",
    formula="%K = 100·(종가 - n봉 최저) / (n봉 최고 - n봉 최저) 를 k만큼 평활 · %D = SMA(%K, d)",
    params=(
        Param("period", "기간", 14, min=1, max=500),
        Param("k_smooth", "%K 평활", 3, min=1, max=100),
        Param("d_smooth", "%D", 3, min=1, max=100),
    ),
    outputs=(
        Output("k", "%K", pane="own", color="accent"),
        Output("d", "%D", pane="own", color="warn"),
    ),
    warmup=lambda p: p["period"] + p["k_smooth"] + p["d_smooth"],
))
def _stoch(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    lowest = m.rolling_min(df["low"], p["period"])
    highest = m.rolling_max(df["high"], p["period"])
    span = (highest - lowest).replace(0.0, np.nan)
    raw_k = 100.0 * (df["close"] - lowest) / span
    k = m.sma(raw_k, p["k_smooth"])
    return pd.DataFrame({"k": k, "d": m.sma(k, p["d_smooth"])})


@indicator(IndicatorSpec(
    key="stochrsi",
    name="스토캐스틱 RSI",
    category="momentum",
    pane="own",
    formula="RSI 를 다시 스토캐스틱에 통과시킨다 - (RSI - RSI최저) / (RSI최고 - RSI최저)",
    params=(
        Param("rsi_period", "RSI 기간", 14, min=2, max=500),
        Param("stoch_period", "스토캐스틱 기간", 14, min=1, max=500),
        Param("k_smooth", "%K 평활", 3, min=1, max=100),
        Param("d_smooth", "%D", 3, min=1, max=100),
    ),
    outputs=(
        Output("k", "%K", pane="own", color="accent"),
        Output("d", "%D", pane="own", color="warn"),
    ),
    warmup=lambda p: p["rsi_period"] * 3 + p["stoch_period"] + p["k_smooth"] + p["d_smooth"],
))
def _stochrsi(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    rsi = _rsi(df["close"], p["rsi_period"])
    lowest = m.rolling_min(rsi, p["stoch_period"])
    highest = m.rolling_max(rsi, p["stoch_period"])
    span = (highest - lowest).replace(0.0, np.nan)
    k = m.sma(100.0 * (rsi - lowest) / span, p["k_smooth"])
    return pd.DataFrame({"k": k, "d": m.sma(k, p["d_smooth"])})


@indicator(IndicatorSpec(
    key="cci",
    name="CCI",
    category="momentum",
    pane="own",
    formula="CCI = (hlc3 - SMA(hlc3, n)) / (0.015 · 평균절대편차)",
    params=(Param("period", "기간", 20, min=2, max=500),),
    outputs=(Output("value", "CCI", pane="own", color="accent"),),
    warmup=lambda p: p["period"] * 2,
))
def _cci(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    n = p["period"]
    tp = m.hlc3(df)
    mean = m.sma(tp, n)
    mad = tp.rolling(n, min_periods=n).apply(
        lambda w: float(np.abs(w - w.mean()).mean()), raw=True
    )
    return pd.DataFrame({"value": (tp - mean) / (0.015 * mad.replace(0.0, np.nan))})


@indicator(IndicatorSpec(
    key="willr",
    name="윌리엄스 %R",
    category="momentum",
    pane="own",
    formula="%R = -100 · (n봉 최고 - 종가) / (n봉 최고 - n봉 최저) · 범위 -100..0",
    params=(Param("period", "기간", 14, min=1, max=500),),
    outputs=(Output("value", "%R", pane="own", color="accent"),),
    warmup=lambda p: p["period"],
))
def _willr(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    highest = m.rolling_max(df["high"], p["period"])
    lowest = m.rolling_min(df["low"], p["period"])
    span = (highest - lowest).replace(0.0, np.nan)
    return pd.DataFrame({"value": -100.0 * (highest - df["close"]) / span})


@indicator(IndicatorSpec(
    key="roc",
    name="변화율 ROC",
    category="momentum",
    pane="own",
    formula="ROC = 100 · (종가 - n봉 전 종가) / n봉 전 종가",
    params=(Param("period", "기간", 12, min=1, max=1000),
            Param("source", "가격", "close", kind="choice", choices=SOURCES)),
    outputs=(Output("value", "ROC", pane="own", color="accent"),),
    warmup=lambda p: p["period"] + 1,
))
def _roc(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    src = m.source(df, p["source"])
    prev = src.shift(p["period"]).replace(0.0, np.nan)
    return pd.DataFrame({"value": 100.0 * (src - prev) / prev})


@indicator(IndicatorSpec(
    key="mfi",
    name="자금흐름지수 MFI",
    category="momentum",
    pane="own",
    formula="자금흐름 = hlc3 · 거래량 을 상승·하락으로 나눠 n봉 합산 · MFI = 100 - 100/(1+비율)",
    params=(Param("period", "기간", 14, min=2, max=500),),
    outputs=(Output("value", "MFI", pane="own", color="accent"),),
    warmup=lambda p: p["period"] + 1,
))
def _mfi(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    n = p["period"]
    tp = m.hlc3(df)
    flow = tp * df["volume"]
    rising = tp.diff() > 0
    falling = tp.diff() < 0
    pos = flow.where(rising, 0.0).rolling(n, min_periods=n).sum()
    neg = flow.where(falling, 0.0).rolling(n, min_periods=n).sum()
    ratio = pos / neg.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + ratio))
    return pd.DataFrame({"value": out.where(neg.ne(0.0) | pos.isna(), 100.0)})


@indicator(IndicatorSpec(
    key="tsi",
    name="참강도지수 TSI",
    category="momentum",
    pane="own",
    formula="TSI = 100 · EMA(EMA(종가변화, 긴), 짧은) / EMA(EMA(|종가변화|, 긴), 짧은)",
    params=(
        Param("long", "긴 기간", 25, min=2, max=500),
        Param("short", "짧은 기간", 13, min=2, max=500),
        Param("signal", "시그널", 13, min=1, max=500),
    ),
    outputs=(
        Output("tsi", "TSI", pane="own", color="accent"),
        Output("signal", "시그널", pane="own", color="warn"),
    ),
    warmup=lambda p: p["long"] + p["short"] + p["signal"],
))
def _tsi(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    change = df["close"].diff()
    smooth = m.ema(m.ema(change, p["long"]), p["short"])
    smooth_abs = m.ema(m.ema(change.abs(), p["long"]), p["short"])
    tsi = 100.0 * smooth / smooth_abs.replace(0.0, np.nan)
    return pd.DataFrame({"tsi": tsi, "signal": m.ema(tsi, p["signal"])})


@indicator(IndicatorSpec(
    key="fisher",
    name="피셔 변환",
    category="momentum",
    pane="own",
    formula="가격을 n봉 범위에서 -1..1 로 정규화(x) 하고 "
            "fish = 0.5·ln((1+x)/(1-x)) + 0.5·직전 fish. "
            "가격 분포를 정규분포에 가깝게 펴서 전환점을 뾰족하게 만든다.",
    params=(Param("period", "기간", 9, min=2, max=500),
            Param("source", "가격", "hl2", kind="choice", choices=SOURCES)),
    outputs=(
        Output("fisher", "피셔", pane="own", color="accent"),
        Output("trigger", "트리거", pane="own", color="warn"),
    ),
    warmup=lambda p: p["period"] * 3,
    source="John F. Ehlers, Using the Fisher Transform (2002)",
))
def _fisher(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    n = p["period"]
    src = m.source(df, p["source"])
    lowest = m.rolling_min(src, n)
    highest = m.rolling_max(src, n)
    span = (highest - lowest).replace(0.0, np.nan)
    raw = (2.0 * ((src - lowest) / span - 0.5)).to_numpy()

    size = len(df)
    value = np.full(size, np.nan)
    fish = np.full(size, np.nan)
    prev_value = 0.0
    prev_fish = 0.0
    for i in range(size):
        if np.isnan(raw[i]):
            continue
        x = 0.33 * raw[i] + 0.67 * prev_value
        # ln((1+x)/(1-x)) 는 |x|=1 에서 발산한다. Ehlers 원문대로 살짝 안쪽으로 묶는다.
        x = float(np.clip(x, -0.999, 0.999))
        f = 0.5 * np.log((1.0 + x) / (1.0 - x)) + 0.5 * prev_fish
        value[i], fish[i] = x, f
        prev_value, prev_fish = x, f
    series = pd.Series(fish, index=df.index)
    return pd.DataFrame({"fisher": series, "trigger": series.shift(1)})


@indicator(IndicatorSpec(
    key="ao",
    name="어썸 오실레이터",
    category="momentum",
    pane="own",
    formula="AO = SMA(hl2, 5) - SMA(hl2, 34)",
    params=(Param("fast", "빠른 기간", 5, min=1, max=500),
            Param("slow", "느린 기간", 34, min=2, max=500)),
    outputs=(Output("value", "AO", draw="histogram", pane="own", color="neutral"),),
    warmup=lambda p: p["slow"],
))
def _ao(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    mid = m.hl2(df)
    return pd.DataFrame({"value": m.sma(mid, p["fast"]) - m.sma(mid, p["slow"])})
