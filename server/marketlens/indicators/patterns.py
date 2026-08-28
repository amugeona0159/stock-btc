"""캔들 패턴.

판정은 전부 봉의 비율로만 한다 - 절대 가격을 쓰면 BTC 와 삼성전자에 다른 기준이 필요해진다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..core.registry import IndicatorSpec, Output, Param, indicator

# 이름 -> (한글명, 방향). 방향은 +1 상승, -1 하락, 0 중립.
PATTERN_META: dict[str, tuple[str, int]] = {
    "doji": ("도지", 0),
    "marubozu_bull": ("상승 마루보즈", 1),
    "marubozu_bear": ("하락 마루보즈", -1),
    "hammer": ("해머", 1),
    "inverted_hammer": ("역해머", 1),
    "hanging_man": ("교수형", -1),
    "shooting_star": ("유성형", -1),
    "bullish_engulfing": ("상승 장악형", 1),
    "bearish_engulfing": ("하락 장악형", -1),
    "piercing": ("관통형", 1),
    "dark_cloud": ("먹구름형", -1),
    "morning_star": ("샛별형", 1),
    "evening_star": ("석별형", -1),
    "three_white_soldiers": ("적삼병", 1),
    "three_black_crows": ("흑삼병", -1),
}


def detect(df: pd.DataFrame, doji_ratio: float = 0.1, shadow_ratio: float = 2.0) -> dict[str, pd.Series]:
    """패턴 이름 -> 그 봉에서 성립하는지의 불리언 시리즈."""
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body = (c - o).abs()
    full = (h - l).replace(0.0, np.nan)
    upper = h - c.combine(o, max)
    lower = c.combine(o, min) - l
    bull = c > o
    bear = c < o

    small_body = body <= doji_ratio * full
    long_lower = lower >= shadow_ratio * body
    long_upper = upper >= shadow_ratio * body
    # 앞 5봉의 방향으로 추세를 본다. 해머와 교수형은 모양이 같고 앞 추세만 다르다.
    downtrend = c.shift(1) < c.shift(5)
    uptrend = c.shift(1) > c.shift(5)

    prev_o, prev_c = o.shift(1), c.shift(1)
    prev_body = (prev_c - prev_o).abs()
    prev_mid = (prev_o + prev_c) / 2.0

    out: dict[str, pd.Series] = {
        "doji": small_body,
        "marubozu_bull": bull & (body >= 0.95 * full),
        "marubozu_bear": bear & (body >= 0.95 * full),
        "hammer": long_lower & (upper <= body) & ~small_body & downtrend,
        "inverted_hammer": long_upper & (lower <= body) & ~small_body & downtrend,
        "hanging_man": long_lower & (upper <= body) & ~small_body & uptrend,
        "shooting_star": long_upper & (lower <= body) & ~small_body & uptrend,
        "bullish_engulfing": bull & (prev_c < prev_o) & (c >= prev_o) & (o <= prev_c) & (body > prev_body),
        "bearish_engulfing": bear & (prev_c > prev_o) & (c <= prev_o) & (o >= prev_c) & (body > prev_body),
        "piercing": bull & (prev_c < prev_o) & (o < prev_c) & (c > prev_mid) & (c < prev_o),
        "dark_cloud": bear & (prev_c > prev_o) & (o > prev_c) & (c < prev_mid) & (c > prev_o),
    }

    # 3봉 패턴: 가운데 봉이 몸통이 작고, 앞뒤가 반대 방향으로 크다.
    o2, c2 = o.shift(2), c.shift(2)
    body2 = (c2 - o2).abs()
    mid_small = prev_body <= 0.5 * body2
    out["morning_star"] = (c2 < o2) & mid_small & bull & (c > (o2 + c2) / 2.0)
    out["evening_star"] = (c2 > o2) & mid_small & bear & (c < (o2 + c2) / 2.0)

    rising3 = bull & (prev_c > prev_o) & (c2 > o2) & (c > prev_c) & (prev_c > c2)
    falling3 = bear & (prev_c < prev_o) & (c2 < o2) & (c < prev_c) & (prev_c < c2)
    out["three_white_soldiers"] = rising3 & (body > 0.5 * full)
    out["three_black_crows"] = falling3 & (body > 0.5 * full)

    return {k: v.fillna(False).astype(bool) for k, v in out.items()}


@indicator(IndicatorSpec(
    key="candle_patterns",
    name="캔들 패턴",
    category="pattern",
    formula="도지·마루보즈·해머·장악형·관통형·샛별형·적삼병 등 15종을 봉의 비율로 판정하고, "
            "그 봉에서 성립한 상승/하락 패턴의 개수를 낸다",
    params=(
        Param("doji_ratio", "도지 몸통 비율", 0.1, kind="float", min=0.01, max=0.5, step=0.01),
        Param("shadow_ratio", "긴 꼬리 배수", 2.0, kind="float", min=1.0, max=10.0, step=0.1),
    ),
    outputs=(
        Output("bullish", "상승 패턴", draw="marker", color="up"),
        Output("bearish", "하락 패턴", draw="marker", color="down"),
    ),
    warmup=lambda p: 6,
))
def _candle_patterns(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    hits = detect(df, p["doji_ratio"], p["shadow_ratio"])
    bullish = pd.Series(0.0, index=df.index)
    bearish = pd.Series(0.0, index=df.index)
    for name, series in hits.items():
        direction = PATTERN_META[name][1]
        if direction > 0:
            bullish += series.astype("float64")
        elif direction < 0:
            bearish += series.astype("float64")
    return pd.DataFrame({
        "bullish": bullish.replace(0.0, np.nan),
        "bearish": bearish.replace(0.0, np.nan),
    })


def latest(df: pd.DataFrame, lookback: int = 3) -> list[dict]:
    """최근 몇 봉에서 성립한 패턴을 사람이 읽는 형태로. 분석 응답이 쓴다."""
    if df.empty:
        return []
    hits = detect(df)
    tail = df.tail(lookback)
    found: list[dict] = []
    for name, series in hits.items():
        label, direction = PATTERN_META[name]
        for idx in tail.index[series.reindex(tail.index).fillna(False).to_numpy()]:
            found.append({
                "key": name,
                "label": label,
                "direction": direction,
                "ts": int(df.loc[idx, "ts"]),
                "bars_ago": int(len(df) - 1 - df.index.get_loc(idx)),
            })
    return sorted(found, key=lambda f: f["bars_ago"])
