"""가격 구조 - 스윙 고/저, 회귀 채널.

피보나치·지지저항이 전부 여기서 나온 스윙을 재료로 쓴다. 스윙 판정을 두 벌 만들면
화면의 되돌림 선과 시그널이 서로 다른 고점을 본다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from ..core.registry import IndicatorSpec, Output, Param, indicator


@dataclass(frozen=True)
class Swing:
    index: int
    ts: int
    price: float
    kind: Literal["high", "low"]


def find_swings(df: pd.DataFrame, left: int = 5, right: int = 5) -> list[Swing]:
    """좌우 봉보다 높은(낮은) 봉을 스윙으로 본다.

    오른쪽 `right` 봉이 다 나오기 전에는 확정하지 않는다 - 그래서 마지막 `right` 봉에서는
    스윙이 나오지 않는다. 이걸 당기면 장중에 생겼다 사라지는 고점이 되고, 그 위에 그린
    피보나치는 새로고침마다 자리를 옮긴다.
    """
    if len(df) < left + right + 1:
        return []
    high, low = df["high"].to_numpy(), df["low"].to_numpy()
    ts = df["ts"].to_numpy()
    found: list[Swing] = []
    for i in range(left, len(df) - right):
        window = slice(i - left, i + right + 1)
        if high[i] == high[window].max() and (high[window] == high[i]).sum() == 1:
            found.append(Swing(i, int(ts[i]), float(high[i]), "high"))
        if low[i] == low[window].min() and (low[window] == low[i]).sum() == 1:
            found.append(Swing(i, int(ts[i]), float(low[i]), "low"))
    found.sort(key=lambda s: s.index)
    return found


def last_leg(swings: list[Swing]) -> tuple[Swing, Swing] | None:
    """가장 최근의 고-저 한 다리. (시작, 끝) 순서로 돌려준다."""
    if len(swings) < 2:
        return None
    end = swings[-1]
    for candidate in reversed(swings[:-1]):
        if candidate.kind != end.kind:
            return candidate, end
    return None


@indicator(IndicatorSpec(
    key="swings",
    name="스윙 고/저",
    category="structure",
    formula="좌우 각각 n봉보다 높은 봉이 스윙 고점, 낮은 봉이 스윙 저점. "
            "오른쪽 n봉이 채워져야 확정된다.",
    params=(Param("left", "왼쪽 봉", 5, min=1, max=100),
            Param("right", "오른쪽 봉", 5, min=1, max=100)),
    outputs=(
        Output("swing_high", "스윙 고점", draw="marker", color="down"),
        Output("swing_low", "스윙 저점", draw="marker", color="up"),
    ),
    warmup=lambda p: p["left"] + p["right"] + 1,
))
def _swings(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    out = pd.DataFrame(
        {"swing_high": np.nan, "swing_low": np.nan}, index=df.index, dtype="float64"
    )
    for swing in find_swings(df, p["left"], p["right"]):
        column = "swing_high" if swing.kind == "high" else "swing_low"
        out.iloc[swing.index, out.columns.get_loc(column)] = swing.price
    return out


@indicator(IndicatorSpec(
    key="linreg_channel",
    name="회귀 채널",
    category="structure",
    formula="최근 n봉 종가에 최소제곱 직선을 맞추고, 잔차 표준편차의 배수만큼 위아래로 벌린다",
    params=(
        Param("period", "기간", 100, min=10, max=5000),
        Param("multiplier", "배수", 2.0, kind="float", min=0.1, max=10.0, step=0.1),
    ),
    outputs=(
        Output("upper", "상단", draw="band", color="neutral", pair="lower"),
        Output("middle", "중심선", color="accent"),
        Output("lower", "하단", draw="band", color="neutral", pair="upper"),
    ),
    warmup=lambda p: p["period"],
))
def _linreg_channel(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    out = pd.DataFrame(
        {"upper": np.nan, "middle": np.nan, "lower": np.nan}, index=df.index, dtype="float64"
    )
    n = min(p["period"], len(df))
    if n < 3:
        return out
    tail = df["close"].to_numpy()[-n:]
    x = np.arange(n, dtype="float64")
    slope, intercept = np.polyfit(x, tail, 1)
    fitted = slope * x + intercept
    band = p["multiplier"] * float(np.std(tail - fitted))
    out.iloc[-n:, out.columns.get_loc("middle")] = fitted
    out.iloc[-n:, out.columns.get_loc("upper")] = fitted + band
    out.iloc[-n:, out.columns.get_loc("lower")] = fitted - band
    return out
