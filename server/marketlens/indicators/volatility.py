"""변동성 지표."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..core.registry import IndicatorSpec, Output, Param, indicator
from . import _math as m
from .trend import MA_KINDS, SOURCES


@indicator(IndicatorSpec(
    key="atr",
    name="ATR",
    category="volatility",
    pane="own",
    formula="TR = max(고-저, |고-전종가|, |저-전종가|) · ATR = RMA(TR, n)",
    params=(Param("period", "기간", 14, min=1, max=500),),
    outputs=(Output("value", "ATR", pane="own", color="accent"),),
    warmup=lambda p: p["period"] * 3,
    source="J. Welles Wilder (1978)",
))
def _atr(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    return pd.DataFrame({"value": m.atr(df, p["period"])})


@indicator(IndicatorSpec(
    key="bbands",
    name="볼린저 밴드",
    category="volatility",
    formula="중심선 = MA(n) · 상·하단 = 중심선 ± 배수·표준편차(n, 모집단) · "
            "%B = (종가-하단)/(상단-하단) · 밴드폭 = (상단-하단)/중심선",
    params=(
        Param("period", "기간", 20, min=2, max=500),
        Param("multiplier", "배수", 2.0, kind="float", min=0.1, max=10.0, step=0.1),
        Param("kind", "중심선", "sma", kind="choice", choices=MA_KINDS),
        Param("source", "가격", "close", kind="choice", choices=SOURCES),
    ),
    outputs=(
        Output("upper", "상단", draw="band", color="neutral", pair="lower"),
        Output("middle", "중심선", color="accent"),
        Output("lower", "하단", draw="band", color="neutral", pair="upper"),
        Output("percent_b", "%B", pane="own", color="accent", optional=True),
        Output("bandwidth", "밴드폭", pane="own", color="warn", optional=True),
    ),
    warmup=lambda p: p["period"] * 2,
    source="John Bollinger (1980s)",
))
def _bbands(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    src = m.source(df, p["source"])
    mid = m.moving_average(src, p["period"], p["kind"])
    dev = p["multiplier"] * m.stdev(src, p["period"])
    upper, lower = mid + dev, mid - dev
    span = (upper - lower).replace(0.0, np.nan)
    return pd.DataFrame({
        "upper": upper,
        "middle": mid,
        "lower": lower,
        "percent_b": (src - lower) / span,
        "bandwidth": span / mid.replace(0.0, np.nan),
    })


@indicator(IndicatorSpec(
    key="keltner",
    name="켈트너 채널",
    category="volatility",
    formula="중심선 = EMA(n) · 상·하단 = 중심선 ± 배수·ATR(a). "
            "볼린저가 표준편차를 쓰는 자리에 ATR 을 쓴다.",
    params=(
        Param("period", "기간", 20, min=2, max=500),
        Param("atr_period", "ATR 기간", 10, min=1, max=500),
        Param("multiplier", "배수", 2.0, kind="float", min=0.1, max=10.0, step=0.1),
    ),
    outputs=(
        Output("upper", "상단", draw="band", color="neutral", pair="lower"),
        Output("middle", "중심선", color="accent"),
        Output("lower", "하단", draw="band", color="neutral", pair="upper"),
    ),
    warmup=lambda p: max(p["period"], p["atr_period"]) * 3,
))
def _keltner(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    mid = m.ema(df["close"], p["period"])
    band = p["multiplier"] * m.atr(df, p["atr_period"])
    return pd.DataFrame({"upper": mid + band, "middle": mid, "lower": mid - band})


@indicator(IndicatorSpec(
    key="donchian",
    name="돈치안 채널",
    category="volatility",
    formula="상단 = n봉 최고가 · 하단 = n봉 최저가 · 중심선 = 둘의 평균",
    params=(Param("period", "기간", 20, min=1, max=500),),
    outputs=(
        Output("upper", "상단", draw="band", color="up", pair="lower"),
        Output("middle", "중심선", color="neutral", optional=True),
        Output("lower", "하단", draw="band", color="down", pair="upper"),
    ),
    warmup=lambda p: p["period"],
))
def _donchian(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    upper = m.rolling_max(df["high"], p["period"])
    lower = m.rolling_min(df["low"], p["period"])
    return pd.DataFrame({"upper": upper, "middle": (upper + lower) / 2.0, "lower": lower})


@indicator(IndicatorSpec(
    key="stdev",
    name="표준편차",
    category="volatility",
    pane="own",
    formula="최근 n봉 종가의 모집단 표준편차(ddof=0)",
    params=(Param("period", "기간", 20, min=2, max=500),
            Param("source", "가격", "close", kind="choice", choices=SOURCES)),
    outputs=(Output("value", "STDEV", pane="own", color="accent"),),
    warmup=lambda p: p["period"],
))
def _stdev(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    return pd.DataFrame({"value": m.stdev(m.source(df, p["source"]), p["period"])})


def _linreg_value(s: pd.Series, period: int) -> pd.Series:
    """창의 마지막 지점에 대한 선형회귀 예측값. TTM 모멘텀이 쓰는 그것."""
    x = np.arange(period, dtype="float64")
    x_mean = x.mean()
    denom = ((x - x_mean) ** 2).sum()

    def fit(window: np.ndarray) -> float:
        y_mean = window.mean()
        slope = ((x - x_mean) * (window - y_mean)).sum() / denom
        return float(y_mean + slope * (period - 1 - x_mean))

    return s.rolling(period, min_periods=period).apply(fit, raw=True)


@indicator(IndicatorSpec(
    key="squeeze",
    name="TTM 스퀴즈",
    category="volatility",
    pane="own",
    formula="볼린저가 켈트너 안으로 들어가면 스퀴즈 ON(변동성 압축) · "
            "모멘텀 = 종가에서 (돈치안 중앙 + EMA)/2 를 뺀 값의 선형회귀. "
            "ON 이 풀리는 봉의 모멘텀 부호가 이탈 방향이다.",
    params=(
        Param("bb_period", "BB 기간", 20, min=2, max=500),
        Param("bb_mult", "BB 배수", 2.0, kind="float", min=0.1, max=10.0, step=0.1),
        Param("kc_period", "KC 기간", 20, min=2, max=500),
        Param("kc_mult", "KC 배수", 1.5, kind="float", min=0.1, max=10.0, step=0.1),
    ),
    outputs=(
        Output("momentum", "모멘텀", draw="histogram", pane="own", color="neutral"),
        Output("squeeze_on", "스퀴즈", draw="histogram", pane="own", color="warn"),
    ),
    warmup=lambda p: max(p["bb_period"], p["kc_period"]) * 3,
    source="John Carter, Mastering the Trade",
))
def _squeeze(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    close = df["close"]
    bb_dev = p["bb_mult"] * m.stdev(close, p["bb_period"])
    bb_mid = m.sma(close, p["bb_period"])
    kc_mid = m.ema(close, p["kc_period"])
    kc_band = p["kc_mult"] * m.atr(df, p["kc_period"])
    on = ((bb_mid + bb_dev) < (kc_mid + kc_band)) & ((bb_mid - bb_dev) > (kc_mid - kc_band))
    on = on.where((bb_dev.notna() & kc_band.notna()), np.nan)

    n = p["kc_period"]
    donchian_mid = (m.rolling_max(df["high"], n) + m.rolling_min(df["low"], n)) / 2.0
    baseline = (donchian_mid + m.sma(close, n)) / 2.0
    momentum = _linreg_value(close - baseline, n)
    return pd.DataFrame({"momentum": momentum, "squeeze_on": on.astype("float64")})
