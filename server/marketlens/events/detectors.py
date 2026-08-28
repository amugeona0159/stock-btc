"""차트 자체 사건.

뉴스가 없어도 차트에는 사건이 남는다 — 급락, 거래량 폭발, 변동성 급등, 신고점.
뉴스 API 가 하나도 안 붙어 있어도 "이런 일이 있었던 때" 를 찾을 수 있어야 한다.

임계값은 전부 **자기 과거 대비 백분위**로 잡는다. 절대값(-5%)으로 자르면 BTC 에서는
흔한 일이 삼성전자에서는 사상 초유의 사건이 된다.

주의: 판정에는 **그 시점까지의 과거만** 쓴다. 전체 구간의 분포로 자르면 미래를 아는
채로 "이때가 급락이었다"고 표시하게 되고, 그 위에 올린 이벤트 스터디가 오염된다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import _math as m
from .schema import Event

# 백분위 임계. 상위 1%면 100봉에 한 번 꼴이라 '사건'이라 부를 만하다.
EXTREME = 0.99
LOOKBACK = 250
MIN_HISTORY = 60


def _rolling_rank(s: pd.Series, lookback: int = LOOKBACK) -> pd.Series:
    """지금 값이 과거 lookback 봉 중 몇 번째 백분위인가. 자기 자신은 빼고 센다."""
    return s.rolling(lookback, min_periods=MIN_HISTORY).apply(
        lambda w: float((w[:-1] < w[-1]).mean()) if len(w) > 1 else np.nan, raw=True
    )


def detect(df: pd.DataFrame, symbol: str, lookback: int = LOOKBACK) -> list[Event]:
    if len(df) < MIN_HISTORY + 5:
        return []

    close = df["close"].astype("float64")
    ret = np.log(close / close.shift(1))
    atr_pct = m.atr(df, 14) / close
    volume = df["volume"].astype("float64")

    scope = f"symbol:{symbol.upper()}"
    out: list[Event] = []

    def push(index, kind_title: str, severity: float, tags: tuple[str, ...], note: str) -> None:
        out.append(Event(
            ts=int(df["ts"].iloc[index]),
            kind="chart",
            title=kind_title,
            source="detector",
            scope=scope,
            severity=float(np.clip(severity, 0.1, 1.0)),
            tags=tags,
            note=note,
        ))

    # --- 한 봉의 급등·급락 ---
    move_rank = _rolling_rank(ret.abs(), lookback)
    for i in np.flatnonzero((move_rank >= EXTREME).to_numpy()):
        change = float(np.expm1(ret.iloc[i]))
        up = change > 0
        push(i, "급등" if up else "급락",
             min(1.0, abs(change) / 0.15),
             ("spike", "up" if up else "down"),
             f"한 봉 {change * 100:+.1f}% — 최근 {lookback}봉 중 상위 1%")

    # --- 거래량 폭발 ---
    volume_rank = _rolling_rank(volume, lookback)
    average = volume.rolling(20, min_periods=5).mean()
    for i in np.flatnonzero((volume_rank >= EXTREME).to_numpy()):
        ratio = float(volume.iloc[i] / average.iloc[i]) if average.iloc[i] > 0 else np.nan
        if not np.isfinite(ratio):
            continue
        push(i, "거래량 폭발", min(1.0, ratio / 6.0), ("volume",),
             f"20봉 평균의 {ratio:.1f}배")

    # --- 변동성 급등 ---
    vol_rank = _rolling_rank(atr_pct, lookback)
    jumped = (vol_rank >= EXTREME) & (vol_rank.shift(1) < 0.9)
    for i in np.flatnonzero(jumped.fillna(False).to_numpy()):
        push(i, "변동성 급등", min(1.0, float(atr_pct.iloc[i]) / 0.05), ("volatility",),
             f"ATR {atr_pct.iloc[i] * 100:.2f}% — 최근 {lookback}봉 중 상위 1%")

    # --- 신고점·신저점 ---
    # 직전까지의 최고/최저를 넘어선 봉만. shift(1) 을 빼면 자기 자신 때문에 항상 참이 된다.
    prior_high = df["high"].rolling(lookback, min_periods=MIN_HISTORY).max().shift(1)
    prior_low = df["low"].rolling(lookback, min_periods=MIN_HISTORY).min().shift(1)
    new_high = (df["high"] > prior_high) & (df["high"].shift(1) <= prior_high.shift(1))
    new_low = (df["low"] < prior_low) & (df["low"].shift(1) >= prior_low.shift(1))
    # 추세가 한쪽으로 가면 몇 봉마다 신고점·신저점이 갱신된다. 그걸 다 사건으로 세면
    # 목록이 이걸로 도배되고, 이벤트 스터디는 같은 흐름을 열 번 센다. 한 번 난 뒤에는
    # 잠시 쉰다 — '돌파했다'는 사건이지 '계속 돌파 중'은 사건이 아니다.
    cooldown = max(5, lookback // 10)
    for column, title, tags, note in (
        (new_high, f"{lookback}봉 신고점", ("breakout", "high"), "직전 최고가 돌파"),
        (new_low, f"{lookback}봉 신저점", ("breakdown", "low"), "직전 최저가 이탈"),
    ):
        last_emitted = -cooldown - 1
        for i in np.flatnonzero(column.fillna(False).to_numpy()):
            if i - last_emitted <= cooldown:
                continue
            push(i, title, 0.5, tags, note)
            last_emitted = i

    # --- 갭 ---
    prev_close = close.shift(1)
    gap = (df["open"] - prev_close) / prev_close
    gap_rank = _rolling_rank(gap.abs(), lookback)
    for i in np.flatnonzero((gap_rank >= EXTREME).to_numpy()):
        size = float(gap.iloc[i])
        if abs(size) < 0.002:
            continue  # 24시간 시장은 갭이 거의 없다. 잡음까지 사건으로 세지 않는다.
        push(i, "갭 " + ("상승" if size > 0 else "하락"), min(1.0, abs(size) / 0.05),
             ("gap",), f"전봉 종가 대비 {size * 100:+.2f}%")

    return _merge_same_bar(out)


def _merge_same_bar(events: list[Event]) -> list[Event]:
    """같은 봉의 탐지들을 한 사건으로 접는다.

    급락이 나면 대개 거래량도 터지고 변동성도 뛴다. 셋을 따로 세면 "사건 300건" 이
    되지만 실제로는 100건이고, 그 숫자 위에서 계산한 이벤트 스터디는 같은 날을
    세 번 세게 된다.
    """
    buckets: dict[int, list[Event]] = {}
    for event in events:
        buckets.setdefault(event.ts, []).append(event)

    merged: list[Event] = []
    for ts in sorted(buckets):
        group = sorted(buckets[ts], key=lambda e: -e.severity)
        if len(group) == 1:
            merged.append(group[0])
            continue
        lead = group[0]
        others = [e.title for e in group[1:]]
        merged.append(Event(
            ts=ts,
            kind="chart",
            title=f"{lead.title} + {', '.join(others)}",
            source=lead.source,
            scope=lead.scope,
            # 여러 신호가 같이 났으면 그만큼 큰 사건이다. 다만 1.0 을 넘지는 않는다.
            severity=float(min(1.0, lead.severity + 0.1 * len(others))),
            tags=tuple(dict.fromkeys(t for e in group for t in e.tags)),
            note=" · ".join(e.note for e in group if e.note),
        ))
    return merged
