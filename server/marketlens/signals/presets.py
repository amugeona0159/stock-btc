"""규칙 모음.

근거 문장은 화면에 그대로 나간다. 숫자를 넣어 "왜 그렇게 봤는지"가 읽히게 쓴다 —
"매수 신호" 만 뜨는 화면은 아무것도 알려주지 않는다.
"""
from __future__ import annotations

import numpy as np

from ..indicators.patterns import PATTERN_META, detect
from .rules import RuleContext, RuleHit, rule


def _price(value: float) -> str:
    """5만짜리 BTC 와 0.4짜리 알트가 같은 문장 틀을 쓴다. 자릿수는 크기가 정한다."""
    magnitude = abs(value)
    digits = 0 if magnitude >= 1000 else 2 if magnitude >= 1 else 6
    return f"{value:,.{digits}f}"


@rule("ichimoku_cloud")
def _ichimoku_cloud(ctx: RuleContext) -> RuleHit | None:
    ind = ctx.ind("ichimoku")
    price = ctx.last(ctx.close)
    a, b = ctx.last(ind["span_a"]), ctx.last(ind["span_b"])
    if not all(np.isfinite(v) for v in (price, a, b)):
        return None
    top, bottom = max(a, b), min(a, b)
    if price > top:
        gap = (price - top) / top * 100
        return RuleHit("ichimoku_cloud", "일목 구름", 1, min(1.0, gap / 3.0),
                       f"가격이 구름 위 {gap:.1f}% — 상승 우위", weight=1.5)
    if price < bottom:
        gap = (bottom - price) / bottom * 100
        return RuleHit("ichimoku_cloud", "일목 구름", -1, min(1.0, gap / 3.0),
                       f"가격이 구름 아래 {gap:.1f}% — 하락 우위", weight=1.5)
    return RuleHit("ichimoku_cloud", "일목 구름", 0, 0.2,
                   "가격이 구름 안 — 방향이 정해지지 않았다", weight=1.5)


@rule("ichimoku_tk_cross")
def _ichimoku_tk(ctx: RuleContext) -> RuleHit | None:
    ind = ctx.ind("ichimoku")
    tenkan, kijun = ind["tenkan"], ind["kijun"]
    if ctx.crossed_up(tenkan, kijun, within=3):
        return RuleHit("ichimoku_tk_cross", "전환/기준 교차", 1, 0.7,
                       "전환선이 기준선을 위로 뚫었다 (호전)")
    if ctx.crossed_down(tenkan, kijun, within=3):
        return RuleHit("ichimoku_tk_cross", "전환/기준 교차", -1, 0.7,
                       "전환선이 기준선을 아래로 뚫었다 (역전)")
    return None


@rule("ma_cross")
def _ma_cross(ctx: RuleContext) -> RuleHit | None:
    fast = ctx.series("ma", "value", period=20, kind="ema")
    slow = ctx.series("ma", "value", period=60, kind="ema")
    if ctx.crossed_up(fast, slow, within=3):
        return RuleHit("ma_cross", "이동평균 교차", 1, 0.8, "20EMA 가 60EMA 를 상향 돌파 (골든크로스)")
    if ctx.crossed_down(fast, slow, within=3):
        return RuleHit("ma_cross", "이동평균 교차", -1, 0.8, "20EMA 가 60EMA 를 하향 돌파 (데드크로스)")
    f, s = ctx.last(fast), ctx.last(slow)
    if not np.isfinite(f) or not np.isfinite(s) or s == 0:
        return None
    spread = (f - s) / s * 100
    direction = 1 if spread > 0 else -1
    return RuleHit("ma_cross", "이동평균 배열", direction, min(1.0, abs(spread) / 5.0),
                   f"20EMA 가 60EMA 보다 {abs(spread):.1f}% "
                   + ("위 (정배열)" if direction > 0 else "아래 (역배열)"), weight=0.6)


@rule("macd_cross")
def _macd_cross(ctx: RuleContext) -> RuleHit | None:
    ind = ctx.ind("macd")
    if ctx.crossed_up(ind["macd"], ind["signal"], within=2):
        return RuleHit("macd_cross", "MACD 교차", 1, 0.7, "MACD 가 시그널선을 상향 돌파")
    if ctx.crossed_down(ind["macd"], ind["signal"], within=2):
        return RuleHit("macd_cross", "MACD 교차", -1, 0.7, "MACD 가 시그널선을 하향 돌파")
    hist = ctx.last(ind["hist"])
    prev = float(ind["hist"].iloc[-2]) if len(ind) > 1 else np.nan
    if not np.isfinite(hist) or not np.isfinite(prev):
        return None
    if hist > 0 and hist > prev:
        return RuleHit("macd_cross", "MACD 모멘텀", 1, 0.4, "MACD 히스토그램이 커지는 중", weight=0.6)
    if hist < 0 and hist < prev:
        return RuleHit("macd_cross", "MACD 모멘텀", -1, 0.4, "MACD 히스토그램이 깊어지는 중", weight=0.6)
    return None


@rule("rsi_zone")
def _rsi_zone(ctx: RuleContext) -> RuleHit | None:
    value = ctx.last(ctx.series("rsi", "value"))
    if not np.isfinite(value):
        return None
    if value >= 70:
        return RuleHit("rsi_zone", "RSI", -1, min(1.0, (value - 70) / 20),
                       f"RSI {value:.0f} — 과매수 구간")
    if value <= 30:
        return RuleHit("rsi_zone", "RSI", 1, min(1.0, (30 - value) / 20),
                       f"RSI {value:.0f} — 과매도 구간")
    return RuleHit("rsi_zone", "RSI", 1 if value > 50 else -1, abs(value - 50) / 50,
                   f"RSI {value:.0f} — 중립대", weight=0.5)


@rule("rsi_divergence")
def _rsi_divergence(ctx: RuleContext) -> RuleHit | None:
    """가격은 새 극값인데 RSI 가 따라가지 못하는 자리.

    스윙을 다시 찾지 않고 `swings` 지표가 잡은 것을 그대로 쓴다 — 화면에 찍히는
    고점과 시그널이 보는 고점이 달라지면 사람이 검증할 수 없다.
    """
    from ..indicators.structure import find_swings

    swings = find_swings(ctx.df, 5, 5)
    rsi = ctx.series("rsi", "value")
    highs = [s for s in swings if s.kind == "high"][-2:]
    lows = [s for s in swings if s.kind == "low"][-2:]

    if len(highs) == 2:
        r0, r1 = rsi.iloc[highs[0].index], rsi.iloc[highs[1].index]
        if highs[1].price > highs[0].price and np.isfinite(r0) and r1 < r0:
            return RuleHit("rsi_divergence", "RSI 다이버전스", -1, 0.8,
                           f"고점은 높아졌는데 RSI 는 {r0:.0f}→{r1:.0f} 로 낮아졌다 (하락 다이버전스)",
                           weight=1.3)
    if len(lows) == 2:
        r0, r1 = rsi.iloc[lows[0].index], rsi.iloc[lows[1].index]
        if lows[1].price < lows[0].price and np.isfinite(r0) and r1 > r0:
            return RuleHit("rsi_divergence", "RSI 다이버전스", 1, 0.8,
                           f"저점은 낮아졌는데 RSI 는 {r0:.0f}→{r1:.0f} 로 높아졌다 (상승 다이버전스)",
                           weight=1.3)
    return None


@rule("supertrend")
def _supertrend(ctx: RuleContext) -> RuleHit | None:
    ind = ctx.ind("supertrend")
    direction = ind["direction"]
    now = ctx.last(direction)
    if not np.isfinite(now):
        return None
    flipped = len(direction) > 1 and direction.iloc[-2] != now
    if flipped:
        return RuleHit("supertrend", "슈퍼트렌드", int(now), 0.9,
                       "슈퍼트렌드가 " + ("상승" if now > 0 else "하락") + "으로 방금 뒤집혔다")
    return RuleHit("supertrend", "슈퍼트렌드", int(now), 0.5,
                   "슈퍼트렌드 " + ("상승" if now > 0 else "하락") + " 유지 중", weight=0.7)


@rule("bollinger")
def _bollinger(ctx: RuleContext) -> RuleHit | None:
    percent_b = ctx.last(ctx.series("bbands", "percent_b"))
    if not np.isfinite(percent_b):
        return None
    if percent_b > 1.0:
        return RuleHit("bollinger", "볼린저", -1, min(1.0, (percent_b - 1.0) * 5),
                       f"%B {percent_b:.2f} — 상단 밴드 이탈")
    if percent_b < 0.0:
        return RuleHit("bollinger", "볼린저", 1, min(1.0, -percent_b * 5),
                       f"%B {percent_b:.2f} — 하단 밴드 이탈")
    return None


@rule("squeeze")
def _squeeze(ctx: RuleContext) -> RuleHit | None:
    ind = ctx.ind("squeeze")
    on = ind["squeeze_on"]
    momentum = ctx.last(ind["momentum"])
    now = ctx.last(on)
    if not np.isfinite(now) or not np.isfinite(momentum):
        return None
    was_on = len(on) > 1 and on.iloc[-2] == 1.0
    if was_on and now == 0.0:
        direction = 1 if momentum > 0 else -1
        return RuleHit("squeeze", "스퀴즈 이탈", direction, 0.9,
                       "변동성 압축이 풀렸다 — 모멘텀 "
                       + ("양수, 위로" if momentum > 0 else "음수, 아래로"), weight=1.4)
    if now == 1.0:
        return RuleHit("squeeze", "스퀴즈", 0, 0.3,
                       "볼린저가 켈트너 안 — 변동성 압축 중, 방향은 아직 없다", weight=0.8)
    return None


@rule("adx_strength")
def _adx_strength(ctx: RuleContext) -> RuleHit | None:
    ind = ctx.ind("adx")
    adx = ctx.last(ind["adx"])
    plus, minus = ctx.last(ind["plus_di"]), ctx.last(ind["minus_di"])
    if not all(np.isfinite(v) for v in (adx, plus, minus)):
        return None
    if adx < 20:
        return RuleHit("adx_strength", "추세 강도", 0, 0.3,
                       f"ADX {adx:.0f} — 추세가 약하다, 방향 신호를 낮춰 볼 것", weight=0.8)
    direction = 1 if plus > minus else -1
    return RuleHit("adx_strength", "추세 강도", direction, min(1.0, adx / 50.0),
                   f"ADX {adx:.0f} · {'+DI' if direction > 0 else '-DI'} 우위 — 추세가 살아 있다")


@rule("fisher")
def _fisher(ctx: RuleContext) -> RuleHit | None:
    ind = ctx.ind("fisher")
    if ctx.crossed_up(ind["fisher"], ind["trigger"], within=2):
        value = ctx.last(ind["fisher"])
        return RuleHit("fisher", "피셔 변환", 1, 0.7,
                       f"피셔({value:.2f})가 트리거를 상향 교차 — 저점 전환 후보")
    if ctx.crossed_down(ind["fisher"], ind["trigger"], within=2):
        value = ctx.last(ind["fisher"])
        return RuleHit("fisher", "피셔 변환", -1, 0.7,
                       f"피셔({value:.2f})가 트리거를 하향 교차 — 고점 전환 후보")
    return None


@rule("stoch")
def _stoch(ctx: RuleContext) -> RuleHit | None:
    ind = ctx.ind("stoch")
    k = ctx.last(ind["k"])
    if not np.isfinite(k):
        return None
    if k < 20 and ctx.crossed_up(ind["k"], ind["d"], within=2):
        return RuleHit("stoch", "스토캐스틱", 1, 0.6, f"과매도(%K {k:.0f})에서 %K 가 %D 를 상향 교차")
    if k > 80 and ctx.crossed_down(ind["k"], ind["d"], within=2):
        return RuleHit("stoch", "스토캐스틱", -1, 0.6, f"과매수(%K {k:.0f})에서 %K 가 %D 를 하향 교차")
    return None


@rule("money_flow")
def _money_flow(ctx: RuleContext) -> RuleHit | None:
    cmf = ctx.last(ctx.series("cmf", "value"))
    if not np.isfinite(cmf) or abs(cmf) < 0.05:
        return None
    direction = 1 if cmf > 0 else -1
    return RuleHit("money_flow", "자금 흐름", direction, min(1.0, abs(cmf) * 4),
                   f"CMF {cmf:+.2f} — 자금이 " + ("들어오는" if direction > 0 else "빠지는") + " 중",
                   weight=0.7)


@rule("candle_pattern")
def _candle_pattern(ctx: RuleContext) -> RuleHit | None:
    hits = detect(ctx.df)
    for name, series in hits.items():
        if not bool(series.iloc[-1]):
            continue
        label, direction = PATTERN_META[name]
        if direction == 0:
            continue
        return RuleHit("candle_pattern", "캔들 패턴", direction, 0.5,
                       f"마지막 봉이 {label}", weight=0.6)
    return None


@rule("fib_level")
def _fib_level(ctx: RuleContext) -> RuleHit | None:
    """되돌림 레벨에 가격이 붙어 있으면 그 자리를 알린다. 방향은 다리가 정한다."""
    ind = ctx.ind("fibonacci")
    price = ctx.last(ctx.close)
    start, end = ctx.last(ind["start"]), ctx.last(ind["end"])
    if not all(np.isfinite(v) for v in (price, start, end)) or price == 0:
        return None
    rising_leg = end > start
    for column, ratio in (("r0382", 0.382), ("r0500", 0.5), ("r0618", 0.618)):
        level = ctx.last(ind[column])
        if np.isfinite(level) and abs(price - level) / price < 0.005:
            direction = 1 if rising_leg else -1
            return RuleHit("fib_level", "피보나치", direction, 0.6,
                           f"{ratio:.3f} 되돌림({_price(level)}) 부근 — "
                           + ("상승 다리의 지지 후보" if rising_leg else "하락 다리의 저항 후보"),
                           weight=0.9)
    return None
