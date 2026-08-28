"""상황 벡터 — "지금이 어떤 상황인가" 를 숫자 한 줄로.

유사구간 검색이 이 벡터의 거리로 과거를 뒤진다. 그래서 두 가지를 지킨다:

1. **모든 축이 무차원이다.** 가격·거래량을 날것으로 넣으면 5만짜리 BTC 와
   7만원짜리 주식이 비교 불가능해지고, 같은 종목도 2년 전과 지금이 딴 세상이 된다.
2. **축의 크기가 서로 비슷하다.** 한 축만 범위가 10배면 그 축이 거리를 독점한다.
   전부 대략 [-1, 1] 안에 들어오게 눌러 둔다.

축은 그룹으로 묶여 있고 그룹마다 가중치가 있다. "모양만 비슷한 구간"과
"상황까지 비슷한 구간"을 구분해서 찾을 수 있어야 하기 때문이다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..indicators import _math as m
from ..indicators import catalog
from . import calendar as cal
from . import regime as reg


@dataclass(frozen=True)
class AxisGroup:
    key: str
    label: str
    weight: float
    note: str


# 기본 가중치. 모양(추세·위치)이 제일 무겁고, 캘린더는 가볍다 —
# 캘린더 효과의 근거가 약하기 때문이다(research.library: calendar_effects).
GROUPS: tuple[AxisGroup, ...] = (
    AxisGroup("trend", "추세", 1.4, "이동평균 대비 위치와 배열"),
    AxisGroup("momentum", "모멘텀", 1.2, "RSI·스토캐스틱·MACD 상태"),
    AxisGroup("position", "구간 내 위치", 1.2, "밴드와 최근 고저 안에서 어디쯤인가"),
    AxisGroup("volatility", "변동성", 1.0, "ATR 수준과 밴드 폭"),
    AxisGroup("volume", "거래량", 0.7, "평소 대비 거래량과 자금 방향"),
    AxisGroup("regime", "레짐", 1.0, "고/저변동, 추세/횡보"),
    AxisGroup("calendar", "캘린더", 0.4, "시각·요일·월중·분기 위치"),
)

GROUP_AXES: dict[str, tuple[str, ...]] = {
    "trend": ("px_over_ema20", "px_over_ema60", "ema_spread", "ema_slope"),
    "momentum": ("rsi", "stoch_k", "macd_hist", "roc_20", "fisher"),
    "position": ("percent_b", "range_position", "cloud_position"),
    "volatility": ("atr_pct", "bandwidth", "squeeze"),
    "volume": ("volume_ratio", "cmf"),
    "regime": ("vol_percentile", "trend_state", "adx"),
    "calendar": ("hour_sin", "hour_cos", "weekday_sin", "weekday_cos",
                 "month_progress", "quarter_progress", "month_sin", "month_cos"),
}

AXES: tuple[str, ...] = tuple(a for group in GROUP_AXES.values() for a in group)


def _squash(s: pd.Series, scale: float) -> pd.Series:
    """비율을 [-1, 1] 로 누른다. 자르지 않고 tanh 로 눌러야 극단값이 한 점에 뭉치지 않는다."""
    return np.tanh(s / scale)


def build(df: pd.DataFrame) -> pd.DataFrame:
    """봉마다의 상황 벡터. 인덱스는 입력과 1:1, 열은 `AXES` 그대로."""
    out = pd.DataFrame(index=df.index, columns=list(AXES), dtype="float64")
    if len(df) < 30:
        return out

    close = df["close"].astype("float64")

    # --- 추세 ---
    ema20 = catalog.compute("ma", df, {"period": 20, "kind": "ema"})["value"]
    ema60 = catalog.compute("ma", df, {"period": 60, "kind": "ema"})["value"]
    out["px_over_ema20"] = _squash(close / ema20 - 1.0, 0.05)
    out["px_over_ema60"] = _squash(close / ema60 - 1.0, 0.10)
    out["ema_spread"] = _squash(ema20 / ema60 - 1.0, 0.05)
    out["ema_slope"] = _squash(np.log(ema20 / ema20.shift(10)), 0.05)

    # --- 모멘텀 ---
    out["rsi"] = catalog.compute("rsi", df, {})["value"] / 50.0 - 1.0
    out["stoch_k"] = catalog.compute("stoch", df, {})["k"] / 50.0 - 1.0
    macd = catalog.compute("macd", df, {})
    out["macd_hist"] = _squash(macd["hist"] / close, 0.005)
    out["roc_20"] = _squash(np.log(close / close.shift(20)), 0.10)
    # 피셔는 이미 ±3 안에서 도는 값이라 나누기만 한다.
    out["fisher"] = np.tanh(catalog.compute("fisher", df, {})["fisher"] / 2.0)

    # --- 구간 내 위치 ---
    bb = catalog.compute("bbands", df, {})
    out["percent_b"] = (bb["percent_b"].clip(-0.5, 1.5) * 2.0 - 1.0).clip(-1, 1)
    high20 = m.rolling_max(df["high"], 20)
    low20 = m.rolling_min(df["low"], 20)
    span20 = (high20 - low20).replace(0.0, np.nan)
    out["range_position"] = ((close - low20) / span20 * 2.0 - 1.0).clip(-1, 1)

    ichi = catalog.compute("ichimoku", df, {})
    cloud_top = ichi[["span_a", "span_b"]].max(axis=1)
    cloud_bottom = ichi[["span_a", "span_b"]].min(axis=1)
    # 구름 위면 +, 아래면 −, 안이면 0 근처. 두께로 나눠 무차원으로 만든다.
    thickness = (cloud_top - cloud_bottom).replace(0.0, np.nan)
    above = (close - cloud_top) / thickness
    below = (close - cloud_bottom) / thickness
    out["cloud_position"] = np.tanh(np.where(close > cloud_top, above,
                                             np.where(close < cloud_bottom, below, 0.0)))

    # --- 변동성 ---
    atr_pct = catalog.compute("atr", df, {})["value"] / close
    out["atr_pct"] = _squash(atr_pct, 0.02)
    out["bandwidth"] = _squash(bb["bandwidth"], 0.08)
    out["squeeze"] = catalog.compute("squeeze", df, {})["squeeze_on"] * 2.0 - 1.0

    # --- 거래량 ---
    volume = df["volume"].astype("float64")
    average = volume.rolling(20, min_periods=5).mean().replace(0.0, np.nan)
    out["volume_ratio"] = _squash(np.log(volume / average), 0.7)
    out["cmf"] = catalog.compute("cmf", df, {})["value"].clip(-1, 1)

    # --- 레짐 ---
    regimes = reg.frame(df)
    out["vol_percentile"] = regimes["vol_percentile"] * 2.0 - 1.0
    out["trend_state"] = regimes["trend"]
    out["adx"] = _squash(regimes["adx"] / 25.0 - 1.0, 1.0)

    # --- 캘린더 ---
    calendar = cal.frame(df["ts"])
    for axis in GROUP_AXES["calendar"]:
        column = calendar[axis]
        # progress 축은 0..1 이라 다른 축과 범위를 맞춰 준다.
        out[axis] = column * 2.0 - 1.0 if axis.endswith("_progress") else column

    return out.replace([np.inf, -np.inf], np.nan)


def weights(overrides: dict[str, float] | None = None) -> np.ndarray:
    """축별 가중치 벡터. 그룹 가중치를 그 그룹의 축 수로 나눠 준다 —
    축이 여덟 개인 캘린더가 축이 둘인 거래량보다 자동으로 무거워지면 안 된다."""
    given = overrides or {}
    values: list[float] = []
    for group in GROUPS:
        weight = float(given.get(group.key, group.weight))
        count = len(GROUP_AXES[group.key])
        values.extend([weight / count] * count)
    return np.asarray(values, dtype="float64")


def describe(df: pd.DataFrame) -> dict:
    """마지막 봉의 상황을 사람이 읽는 형태로. 예측 근거 문장에 쓴다."""
    if df.empty:
        return {"available": False}
    state = reg.latest(df)
    vector = build(df)
    row = vector.iloc[-1] if len(vector) else None

    def value(axis: str) -> float | None:
        if row is None or not np.isfinite(row.get(axis, np.nan)):
            return None
        return round(float(row[axis]), 3)

    return {
        "available": row is not None,
        "calendar": cal.describe(int(df["ts"].iloc[-1])),
        "regime": state.to_dict(),
        "axes": {axis: value(axis) for axis in AXES},
        "groups": [
            {"key": g.key, "label": g.label, "weight": g.weight, "note": g.note}
            for g in GROUPS
        ],
    }
