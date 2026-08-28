"""추세 지표."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..core.registry import IndicatorSpec, Output, Param, indicator
from . import _math as m

MA_KINDS = ("sma", "ema", "wma", "hma", "rma")
SOURCES = ("close", "open", "high", "low", "hl2", "hlc3", "ohlc4")


@indicator(IndicatorSpec(
    key="ma",
    name="이동평균",
    category="trend",
    formula="SMA=최근 n봉 평균 · EMA=SMA로 시드한 뒤 a=2/(n+1) 재귀 · "
            "WMA=1..n 선형가중 · HMA=WMA(2·WMA(n/2)-WMA(n), sqrt n) · RMA=a=1/n(Wilder)",
    params=(
        Param("period", "기간", 20, min=1, max=1000),
        Param("kind", "종류", "ema", kind="choice", choices=MA_KINDS),
        Param("source", "가격", "close", kind="choice", choices=SOURCES),
    ),
    outputs=(Output("value", "MA", color="accent"),),
    warmup=lambda p: p["period"] * (3 if p["kind"] == "hma" else 1),
))
def _ma(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    src = m.source(df, p["source"])
    return pd.DataFrame({"value": m.moving_average(src, p["period"], p["kind"])})


@indicator(IndicatorSpec(
    key="dema",
    name="이중지수이동평균",
    category="trend",
    formula="DEMA = 2·EMA(n) - EMA(EMA(n), n)",
    params=(Param("period", "기간", 20, min=2, max=1000),
            Param("source", "가격", "close", kind="choice", choices=SOURCES)),
    outputs=(Output("value", "DEMA", color="accent"),),
    warmup=lambda p: p["period"] * 2,
))
def _dema(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    src = m.source(df, p["source"])
    e1 = m.ema(src, p["period"])
    return pd.DataFrame({"value": 2 * e1 - m.ema(e1, p["period"])})


@indicator(IndicatorSpec(
    key="tema",
    name="삼중지수이동평균",
    category="trend",
    formula="TEMA = 3·EMA - 3·EMA(EMA) + EMA(EMA(EMA))",
    params=(Param("period", "기간", 20, min=2, max=1000),
            Param("source", "가격", "close", kind="choice", choices=SOURCES)),
    outputs=(Output("value", "TEMA", color="accent"),),
    warmup=lambda p: p["period"] * 3,
))
def _tema(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    src = m.source(df, p["source"])
    e1 = m.ema(src, p["period"])
    e2 = m.ema(e1, p["period"])
    return pd.DataFrame({"value": 3 * e1 - 3 * e2 + m.ema(e2, p["period"])})


@indicator(IndicatorSpec(
    key="macd",
    name="MACD",
    category="trend",
    pane="own",
    formula="MACD = EMA(빠름) - EMA(느림) · 시그널 = EMA(MACD, s) · 히스토그램 = MACD - 시그널",
    params=(
        Param("fast", "빠른 기간", 12, min=1, max=500),
        Param("slow", "느린 기간", 26, min=2, max=500),
        Param("signal", "시그널", 9, min=1, max=500),
        Param("source", "가격", "close", kind="choice", choices=SOURCES),
    ),
    outputs=(
        Output("macd", "MACD", pane="own", color="accent"),
        Output("signal", "시그널", pane="own", color="warn"),
        Output("hist", "히스토그램", draw="histogram", pane="own", color="neutral"),
    ),
    warmup=lambda p: p["slow"] + p["signal"],
))
def _macd(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    src = m.source(df, p["source"])
    line = m.ema(src, p["fast"]) - m.ema(src, p["slow"])
    sig = m.ema(line, p["signal"])
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


@indicator(IndicatorSpec(
    key="adx",
    name="ADX / DMI",
    category="trend",
    pane="own",
    formula="+DM·-DM 을 Wilder 평활한 뒤 ATR 로 나눠 ±DI · "
            "DX = 100·|+DI - -DI| / (+DI + -DI) · ADX = RMA(DX, n)",
    params=(Param("period", "기간", 14, min=2, max=200),
            Param("smooth", "ADX 평활", 14, min=2, max=200)),
    outputs=(
        Output("adx", "ADX", pane="own", color="accent"),
        Output("plus_di", "+DI", pane="own", color="up"),
        Output("minus_di", "-DI", pane="own", color="down"),
    ),
    warmup=lambda p: p["period"] + p["smooth"] * 2,
))
def _adx(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    n = p["period"]
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = m.rma(m.true_range(df), n)
    safe_tr = tr.replace(0.0, np.nan)
    plus_di = 100.0 * m.rma(plus_dm, n) / safe_tr
    minus_di = 100.0 * m.rma(minus_dm, n) / safe_tr
    total = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / total
    return pd.DataFrame({"adx": m.rma(dx, p["smooth"]), "plus_di": plus_di, "minus_di": minus_di})


@indicator(IndicatorSpec(
    key="supertrend",
    name="슈퍼트렌드",
    category="trend",
    formula="밴드 = hl2 ± 배수·ATR(n) 을 한 방향으로만 조이고, 종가가 반대 밴드를 뚫으면 추세 전환",
    params=(Param("period", "ATR 기간", 10, min=1, max=200),
            Param("multiplier", "배수", 3.0, kind="float", min=0.1, max=20.0, step=0.1)),
    outputs=(
        Output("value", "슈퍼트렌드", draw="step", color="accent"),
        Output("direction", "방향", pane="own", color="neutral", optional=True),
    ),
    warmup=lambda p: p["period"] * 3,
))
def _supertrend(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    n, mult = p["period"], p["multiplier"]
    atr = m.atr(df, n).to_numpy()
    mid = m.hl2(df).to_numpy()
    close = df["close"].to_numpy()
    size = len(df)
    upper = mid + mult * atr
    lower = mid - mult * atr
    f_up = np.full(size, np.nan)
    f_low = np.full(size, np.nan)
    direction = np.full(size, np.nan)
    started = False
    for i in range(size):
        if np.isnan(atr[i]):
            continue
        if not started:
            f_up[i], f_low[i], direction[i] = upper[i], lower[i], 1.0
            started = True
            continue
        # 밴드는 한 방향으로만 조인다 - 종가가 뚫기 전까지 뒤로 물러서지 않는다.
        f_up[i] = upper[i] if (upper[i] < f_up[i - 1] or close[i - 1] > f_up[i - 1]) else f_up[i - 1]
        f_low[i] = lower[i] if (lower[i] > f_low[i - 1] or close[i - 1] < f_low[i - 1]) else f_low[i - 1]
        if close[i] > f_up[i - 1]:
            direction[i] = 1.0
        elif close[i] < f_low[i - 1]:
            direction[i] = -1.0
        else:
            direction[i] = direction[i - 1]
    value = np.where(direction == 1.0, f_low, f_up)
    value[np.isnan(direction)] = np.nan
    return pd.DataFrame({"value": value, "direction": direction}, index=df.index)


@indicator(IndicatorSpec(
    key="psar",
    name="파라볼릭 SAR",
    category="trend",
    formula="SAR(t+1) = SAR(t) + AF·(EP - SAR(t)) · 새 극값마다 AF 를 step 만큼 올려 max 까지",
    params=(
        Param("step", "가속 계수", 0.02, kind="float", min=0.001, max=0.5, step=0.001),
        Param("max_step", "최대 가속", 0.2, kind="float", min=0.01, max=1.0, step=0.01),
    ),
    outputs=(Output("value", "PSAR", draw="marker", color="accent"),),
    warmup=lambda p: 5,
))
def _psar(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    high, low = df["high"].to_numpy(), df["low"].to_numpy()
    size = len(df)
    out = np.full(size, np.nan)
    if size < 2:
        return pd.DataFrame({"value": out}, index=df.index)
    step, cap = p["step"], p["max_step"]
    rising = bool(high[1] >= high[0])
    sar = low[0] if rising else high[0]
    ep = high[1] if rising else low[1]
    af = step
    for i in range(1, size):
        sar = sar + af * (ep - sar)
        if rising:
            # SAR 은 직전 두 봉의 저가를 넘어서지 못한다.
            sar = min(sar, low[i - 1], low[max(0, i - 2)])
            if low[i] < sar:
                rising, sar, ep, af = False, ep, low[i], step
            elif high[i] > ep:
                ep, af = high[i], min(af + step, cap)
        else:
            sar = max(sar, high[i - 1], high[max(0, i - 2)])
            if high[i] > sar:
                rising, sar, ep, af = True, ep, high[i], step
            elif low[i] < ep:
                ep, af = low[i], min(af + step, cap)
        out[i] = sar
    return pd.DataFrame({"value": out}, index=df.index)


@indicator(IndicatorSpec(
    key="ichimoku",
    name="일목균형표",
    category="trend",
    formula="전환선=(9고+9저)/2 · 기준선=(26고+26저)/2 · "
            "선행스팬A=(전환+기준)/2 를 26봉 앞으로 · 선행스팬B=(52고+52저)/2 를 26봉 앞으로 · "
            "후행스팬=종가를 26봉 뒤로. 스팬A와 B 사이가 구름대.",
    params=(
        Param("tenkan", "전환선", 9, min=1, max=200),
        Param("kijun", "기준선", 26, min=1, max=400),
        Param("senkou", "선행스팬B", 52, min=1, max=600),
        Param("displacement", "이동", 26, min=1, max=200),
    ),
    outputs=(
        Output("tenkan", "전환선", color="accent"),
        Output("kijun", "기준선", color="warn"),
        # 이미 앞으로 밀린 값이다. 지금 봉의 가격이 구름 위인지 아래인지는 이 두 열로 본다.
        Output("span_a", "선행스팬A", draw="cloud", color="up", pair="span_b"),
        Output("span_b", "선행스팬B", draw="cloud", color="down", pair="span_a"),
        Output("chikou", "후행스팬", color="neutral"),
        # 아직 가격이 없는 미래 구간의 구름. API 가 미래 시각을 붙여 내보낸다.
        Output("span_a_lead", "선행스팬A(미래)", draw="cloud", color="up",
               pair="span_b_lead", offset=26, offset_param="displacement"),
        Output("span_b_lead", "선행스팬B(미래)", draw="cloud", color="down",
               pair="span_a_lead", offset=26, offset_param="displacement"),
    ),
    warmup=lambda p: p["senkou"] + p["displacement"],
    source="一目均衡表 (細田悟一, 1969)",
))
def _ichimoku(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    def mid(period: int) -> pd.Series:
        return (m.rolling_max(df["high"], period) + m.rolling_min(df["low"], period)) / 2.0

    disp = p["displacement"]
    tenkan = mid(p["tenkan"])
    kijun = mid(p["kijun"])
    lead_a = (tenkan + kijun) / 2.0
    lead_b = mid(p["senkou"])
    return pd.DataFrame({
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": lead_a.shift(disp),
        "span_b": lead_b.shift(disp),
        # 후행스팬은 26봉 전 자리에 찍히므로 여기 담기는 값은 26봉 뒤의 종가다.
        # 마지막 26봉이 NaN 인 게 정상이고, 그래서 시그널이 미래를 훔쳐볼 수 없다.
        "chikou": df["close"].shift(-disp),
        "span_a_lead": lead_a,
        "span_b_lead": lead_b,
    })
