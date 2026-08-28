"""지표를 피처 행렬로.

가격을 그대로 넣지 않는다. 5만 달러짜리 BTC 와 7만원짜리 주식이 같은 모델을 쓰려면
모든 피처가 비율이거나 이미 정규화된 값이어야 한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ...core.candle import closed_only
from ...indicators import catalog


def build(df: pd.DataFrame) -> pd.DataFrame:
    """확정 봉 기준 피처. 인덱스는 입력의 확정 봉과 1:1."""
    closed = closed_only(df).reset_index(drop=True)
    if len(closed) < 60:
        return pd.DataFrame()

    close = closed["close"].astype("float64")
    out = pd.DataFrame(index=closed.index)
    out["ts"] = closed["ts"]

    # --- 수익률 계열 (그 자체로 무차원) ---
    logret = np.log(close / close.shift(1))
    out["ret_1"] = logret
    for span in (3, 5, 10, 20):
        out[f"ret_{span}"] = np.log(close / close.shift(span))
    out["ret_vol_20"] = logret.rolling(20).std(ddof=1)

    # --- 지표는 전부 가격 대비 비율이나 0..100 스케일로 ---
    ema20 = catalog.compute("ma", closed, {"period": 20, "kind": "ema"})["value"]
    ema60 = catalog.compute("ma", closed, {"period": 60, "kind": "ema"})["value"]
    out["px_over_ema20"] = close / ema20 - 1.0
    out["px_over_ema60"] = close / ema60 - 1.0
    out["ema_spread"] = ema20 / ema60 - 1.0

    out["rsi"] = catalog.compute("rsi", closed, {})["value"] / 100.0
    stoch = catalog.compute("stoch", closed, {})
    out["stoch_k"] = stoch["k"] / 100.0
    out["stoch_d"] = stoch["d"] / 100.0

    macd = catalog.compute("macd", closed, {})
    out["macd_hist_norm"] = macd["hist"] / close
    out["macd_norm"] = macd["macd"] / close

    adx = catalog.compute("adx", closed, {})
    out["adx"] = adx["adx"] / 100.0
    out["di_spread"] = (adx["plus_di"] - adx["minus_di"]) / 100.0

    bb = catalog.compute("bbands", closed, {})
    out["percent_b"] = bb["percent_b"]
    out["bandwidth"] = bb["bandwidth"]

    atr = catalog.compute("atr", closed, {})["value"]
    out["atr_pct"] = atr / close

    ichi = catalog.compute("ichimoku", closed, {})
    cloud_top = ichi[["span_a", "span_b"]].max(axis=1)
    cloud_bottom = ichi[["span_a", "span_b"]].min(axis=1)
    out["px_over_cloud"] = close / cloud_top - 1.0
    out["px_under_cloud"] = close / cloud_bottom - 1.0
    out["tk_spread"] = (ichi["tenkan"] - ichi["kijun"]) / close

    out["fisher"] = catalog.compute("fisher", closed, {})["fisher"]
    out["cmf"] = catalog.compute("cmf", closed, {})["value"]
    out["mfi"] = catalog.compute("mfi", closed, {})["value"] / 100.0

    volume = closed["volume"].astype("float64")
    out["vol_ratio"] = volume / volume.rolling(20).mean().replace(0.0, np.nan)

    return out.replace([np.inf, -np.inf], np.nan)


FEATURE_COLUMNS = [
    "ret_1", "ret_3", "ret_5", "ret_10", "ret_20", "ret_vol_20",
    "px_over_ema20", "px_over_ema60", "ema_spread",
    "rsi", "stoch_k", "stoch_d", "macd_hist_norm", "macd_norm",
    "adx", "di_spread", "percent_b", "bandwidth", "atr_pct",
    "px_over_cloud", "px_under_cloud", "tk_spread",
    "fisher", "cmf", "mfi", "vol_ratio",
]
