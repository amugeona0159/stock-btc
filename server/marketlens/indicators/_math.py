"""지표들이 공유하는 계산 조각.

평활 방식은 여기서 한 번만 정한다. EMA 를 어디선 첫 값으로, 어디선 SMA 로 시드하면
같은 화면 안에서 20EMA 두 개가 다르게 그려진다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _seeded(s: pd.Series, alpha: float, period: int) -> pd.Series:
    """앞 `period` 개의 단순평균으로 시드한 재귀 평활.

    TradingView `ta.ema` / `ta.rma` 와 같은 관례다. 앞머리 NaN 은 건너뛰고
    첫 유효값부터 센다 — 지표를 지표에 물릴 때(DEMA·StochRSI) 앞이 비어 오기 때문.
    """
    valid = s.dropna()
    out = pd.Series(np.nan, index=s.index, dtype="float64")
    if len(valid) < period:
        return out
    seeded = valid.astype("float64").copy()
    seeded.iloc[: period - 1] = np.nan
    seeded.iloc[period - 1] = valid.iloc[:period].mean()
    return seeded.ewm(alpha=alpha, adjust=False, ignore_na=False).mean().reindex(s.index)


def sma(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period, min_periods=period).mean()


def ema(s: pd.Series, period: int) -> pd.Series:
    return _seeded(s, 2.0 / (period + 1.0), period)


def rma(s: pd.Series, period: int) -> pd.Series:
    """Wilder 평활(alpha = 1/n). RSI·ATR·ADX 가 쓰는 그것."""
    return _seeded(s, 1.0 / period, period)


def wma(s: pd.Series, period: int) -> pd.Series:
    weights = np.arange(1, period + 1, dtype="float64")
    denom = weights.sum()
    return s.rolling(period, min_periods=period).apply(
        lambda w: float(np.dot(w, weights) / denom), raw=True
    )


def hma(s: pd.Series, period: int) -> pd.Series:
    half, root = max(1, period // 2), max(1, int(round(np.sqrt(period))))
    return wma(2.0 * wma(s, half) - wma(s, period), root)


def stdev(s: pd.Series, period: int) -> pd.Series:
    """모집단 표준편차(ddof=0). 볼린저의 관례다 — 표본(ddof=1)을 쓰면 밴드가 넓어진다."""
    return s.rolling(period, min_periods=period).std(ddof=0)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    tr = ranges.max(axis=1)
    tr.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0] if len(df) else np.nan
    return tr


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    return rma(true_range(df), period)


def hl2(df: pd.DataFrame) -> pd.Series:
    return (df["high"] + df["low"]) / 2.0


def hlc3(df: pd.DataFrame) -> pd.Series:
    return (df["high"] + df["low"] + df["close"]) / 3.0


def ohlc4(df: pd.DataFrame) -> pd.Series:
    return (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0


SOURCES = {"close": lambda d: d["close"], "open": lambda d: d["open"],
           "high": lambda d: d["high"], "low": lambda d: d["low"],
           "hl2": hl2, "hlc3": hlc3, "ohlc4": ohlc4}


def source(df: pd.DataFrame, name: str) -> pd.Series:
    try:
        return SOURCES[name](df).astype("float64")
    except KeyError:
        raise ValueError(f"알 수 없는 가격 소스: {name!r} ({sorted(SOURCES)})") from None


def moving_average(s: pd.Series, period: int, kind: str) -> pd.Series:
    fns = {"sma": sma, "ema": ema, "wma": wma, "hma": hma, "rma": rma}
    try:
        return fns[kind](s, period)
    except KeyError:
        raise ValueError(f"알 수 없는 이동평균: {kind!r} ({sorted(fns)})") from None


def rolling_max(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period, min_periods=period).max()


def rolling_min(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period, min_periods=period).min()


def crossover(a: pd.Series, b: pd.Series) -> pd.Series:
    """a 가 b 를 아래에서 위로 뚫은 봉에 True."""
    return (a > b) & (a.shift(1) <= b.shift(1))


def crossunder(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))
