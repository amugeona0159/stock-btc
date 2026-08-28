"""통계적 구간 예측.

방향 하나만 찍는 것보다 "어디까지 갈 수 있는가"가 실제로 쓸모 있다. 손절과 목표가는
구간에서 나오지 화살표에서 나오지 않는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..core.candle import closed_only
from ..core.timeframe import to_ms
from ..indicators import _math as m

# 신뢰수준 -> 정규분포 z. 표를 여기 박아 두는 게 scipy 를 끌어오는 것보다 낫다.
Z = {0.5: 0.674, 0.68: 0.994, 0.8: 1.282, 0.9: 1.645, 0.95: 1.960}

# 드리프트는 이 비율만큼만 믿는다. 최근 표류를 그대로 미래에 곱하면
# 며칠 오른 종목이 영원히 오르는 예측이 나온다.
DRIFT_SHRINK = 0.3


def project(
    df: pd.DataFrame,
    horizon: int = 10,
    lookback: int = 200,
    levels: tuple[float, ...] = (0.5, 0.8, 0.95),
    timeframe: str = "1d",
) -> dict:
    """N봉 뒤 가격 구간.

    로그수익률이 독립이라고 보고 분산을 시간에 비례시킨다(sigma*sqrt(N)). 실제 시장은
    변동성이 뭉치므로 이건 낙관적인 하한이다 — ATR 쪽 구간을 같이 내서 서로 대볼 수 있게 한다.
    """
    closed = closed_only(df)
    if len(closed) < 30:
        return {"available": False, "reason": "봉이 30개보다 적어 통계 구간을 낼 수 없다"}

    close = closed["close"].astype("float64")
    returns = np.log(close / close.shift(1)).dropna().tail(lookback)
    if len(returns) < 20 or returns.std(ddof=1) == 0:
        return {"available": False, "reason": "수익률 변동이 없어 구간을 낼 수 없다"}

    sigma = float(returns.std(ddof=1))
    drift = float(returns.mean()) * DRIFT_SHRINK
    last = float(close.iloc[-1])
    last_ts = int(closed["ts"].iloc[-1])
    step = to_ms(timeframe)

    spread = sigma * np.sqrt(horizon)
    mid = last * np.exp(drift * horizon)
    bands = {
        f"{int(level * 100)}": {
            "low": float(last * np.exp(drift * horizon - Z[level] * spread)),
            "high": float(last * np.exp(drift * horizon + Z[level] * spread)),
        }
        for level in levels
    }

    atr = float(m.atr(closed, 14).iloc[-1])
    atr_band = None
    if np.isfinite(atr):
        reach = atr * np.sqrt(horizon)
        atr_band = {"low": last - reach, "high": last + reach, "atr": atr}

    return {
        "available": True,
        "horizon": horizon,
        "timeframe": timeframe,
        "last": last,
        "lastTs": last_ts,
        "targetTs": last_ts + step * horizon,
        "mid": float(mid),
        "bands": bands,
        "atrBand": atr_band,
        "sigmaPerBar": sigma,
        "driftPerBar": drift,
        "expectedMovePct": float((mid / last - 1.0) * 100.0),
    }


def monte_carlo(
    df: pd.DataFrame,
    horizon: int = 10,
    paths: int = 2000,
    lookback: int = 500,
    seed: int = 20260828,
) -> dict:
    """과거 수익률을 부트스트랩해 경로를 굴린다.

    정규분포를 가정하지 않는다 — 실제 분포의 두꺼운 꼬리가 그대로 반영되는 게 요점이다.
    시드를 박아 같은 입력이면 같은 답이 나오게 한다. 새로고침마다 예측이 바뀌면 못 믿는다.
    """
    closed = closed_only(df)
    if len(closed) < 60:
        return {"available": False, "reason": "봉이 60개보다 적어 시뮬레이션을 못 돌린다"}

    close = closed["close"].astype("float64")
    returns = np.log(close / close.shift(1)).dropna().tail(lookback).to_numpy()
    if returns.size < 30:
        return {"available": False, "reason": "수익률 표본이 모자란다"}

    rng = np.random.default_rng(seed)
    draws = rng.choice(returns, size=(paths, horizon), replace=True)
    last = float(close.iloc[-1])
    finals = last * np.exp(draws.sum(axis=1))

    percentiles = [5, 25, 50, 75, 95]
    return {
        "available": True,
        "horizon": horizon,
        "paths": paths,
        "last": last,
        "percentiles": {
            str(p): float(np.percentile(finals, p)) for p in percentiles
        },
        "probUp": float((finals > last).mean()),
        "expected": float(finals.mean()),
    }
