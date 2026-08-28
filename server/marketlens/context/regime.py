"""레짐 — 지금이 어떤 국면인가.

방향(추세/횡보)과 크기(변동성)를 나눠서 잰다. 둘을 섞으면 "크게 움직이는 횡보"와
"조용한 추세"를 구분할 수 없는데, 이 둘은 이후 전개가 완전히 다르다.

레벨은 **자기 과거 대비 백분위**로 정한다. 절대값으로 자르면 BTC 와 삼성전자에
다른 기준이 필요해지고, 같은 종목도 몇 년 지나면 기준이 어긋난다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..indicators import _math as m

VOLATILITY_LABELS = {0: "저변동", 1: "보통", 2: "고변동"}
TREND_LABELS = {-1: "하락추세", 0: "횡보", 1: "상승추세"}

# 백분위 경계. 3분할이면 1/3 씩이 자연스럽지만, 실전에서 '고변동'은 더 좁게 잡아야
# 경고로서 의미가 있다.
VOL_BANDS = (0.35, 0.75)
# ADX 20 미만은 추세가 없다고 보는 관행을 따른다(Wilder).
ADX_TREND_FLOOR = 20.0


@dataclass(frozen=True)
class RegimeState:
    volatility: int
    trend: int
    vol_percentile: float
    adx: float

    def to_dict(self) -> dict:
        return {
            "volatility": self.volatility,
            "volatilityLabel": VOLATILITY_LABELS[self.volatility],
            "trend": self.trend,
            "trendLabel": TREND_LABELS[self.trend],
            "volPercentile": round(self.vol_percentile, 3),
            "adx": round(self.adx, 1) if np.isfinite(self.adx) else None,
        }


def frame(df: pd.DataFrame, lookback: int = 250) -> pd.DataFrame:
    """봉마다의 레짐. 백분위는 **그 시점까지의 과거**로만 잰다 — 전체 구간으로 재면
    미래를 훔쳐보게 되고, 유사구간 검색이 미래를 아는 채로 사례를 고른다."""
    atr_pct = m.atr(df, 14) / df["close"]
    # expanding 이 아니라 rolling 을 쓰는 이유: 몇 년 전 변동성 수준과 비교하면
    # 시장 구조가 바뀐 것까지 '지금이 고변동'으로 읽는다.
    vol_pct = atr_pct.rolling(lookback, min_periods=30).apply(
        lambda w: float((w[:-1] < w[-1]).mean()) if len(w) > 1 else np.nan, raw=True
    )

    adx_frame = _adx(df)
    slope = np.log(df["close"] / df["close"].shift(20))

    out = pd.DataFrame(index=df.index)
    out["vol_percentile"] = vol_pct
    out["adx"] = adx_frame["adx"]
    out["volatility"] = np.select(
        [vol_pct < VOL_BANDS[0], vol_pct < VOL_BANDS[1]], [0.0, 1.0], default=2.0
    )
    out.loc[vol_pct.isna(), "volatility"] = np.nan

    trending = adx_frame["adx"] >= ADX_TREND_FLOOR
    out["trend"] = np.select(
        [trending & (slope > 0), trending & (slope <= 0)], [1.0, -1.0], default=0.0
    )
    out.loc[adx_frame["adx"].isna() | slope.isna(), "trend"] = np.nan
    return out


def _adx(df: pd.DataFrame) -> pd.DataFrame:
    from ..indicators import catalog
    return catalog.compute("adx", df, {})


def latest(df: pd.DataFrame, lookback: int = 250) -> RegimeState:
    states = frame(df, lookback)
    if states.empty:
        return RegimeState(1, 0, float("nan"), float("nan"))
    row = states.iloc[-1]
    volatility = int(row["volatility"]) if np.isfinite(row["volatility"]) else 1
    trend = int(row["trend"]) if np.isfinite(row["trend"]) else 0
    return RegimeState(volatility, trend, float(row["vol_percentile"]), float(row["adx"]))
