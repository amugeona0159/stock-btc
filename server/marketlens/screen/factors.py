"""팩터 — 종목을 줄 세우는 축.

**팩터를 새로 만들지 않는다.** 학습 표(`forecast.ml.dataset`)가 이미 봉마다 54축을
인과적으로 뽑아 두었고, `tests/test_asof.py` 가 "origin 뒤를 흔들어도 그 시점 값이
안 변한다"로 그걸 지킨다. 여기서 축을 다시 만들면 그 보증 밖으로 나간다.

여기가 하는 일은 **고르는 것**뿐이다: 54축 중 종목 간 비교가 말이 되는 것만 추린다.
캘린더 축(hour_sin 등)은 뺀다 — 같은 시각이면 모든 종목이 같은 값이라 순위가 안 갈린다.

## 부호를 정하지 않는다

"RSI 가 낮으면 산다" 같은 방향은 여기에 안 적는다. **부호는 재서 정한다**
(`screen/ic.py`). 미리 정하면 그건 측정이 아니라 내 믿음이고, 이 저장소의 규칙은
"재고 나서 넣는다"다.

## 두 갈래로 잰다

- **방향**(`direction`) — 앞으로 오를까 내릴까. 랭크 IC 로 잰다.
- **변동**(`move`) — 앞으로 많이 움직일까. |수익률| 과의 랭크 IC 로 잰다.

"관심있게 볼 종목"은 대개 뒤쪽이다. 방향을 못 맞혀도 크게 움직일 종목은 볼 값어치가
있고, 실제로 변동 쪽이 훨씬 잘 맞는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..forecast.ml import dataset

# 종목 간 비교가 말이 되는 축만. 캘린더 축은 같은 시각이면 전 종목이 같은 값이라 뺐다.
CANDIDATES: tuple[str, ...] = (
    # 추세·모멘텀
    "px_over_ema20", "px_over_ema60", "ema_spread", "ema_slope",
    "rsi", "stoch_k", "macd_hist", "roc_20", "fisher",
    # 위치
    "percent_b", "range_position", "cloud_position",
    # 변동성·압축
    "atr_pct", "bandwidth", "squeeze", "vol_percentile",
    # 거래량
    "volume_ratio", "cmf",
    # 레짐
    "trend_state", "adx",
    # 유사구간이 실제로 간 곳
    "analog_median", "analog_mean", "analog_prob_up",
    "analog_spread", "analog_corr_max", "analog_corr_mean",
    # 사건
    "event_recency", "event_severity", "event_chart", "event_macro", "event_scheduled",
    # 관심도
    "attention_z", "attention_change", "attention_spike",
    # 미시구조
    "clv", "signed_volume", "pressure", "wick_bias",
    # 시장 대비
    "market_ret_5", "market_ret_20", "rel_strength_5", "rel_strength_20",
    "beta", "market_vol",
)

# 사람이 읽을 이름. 없는 축은 키를 그대로 쓴다.
LABEL: dict[str, str] = {
    "px_over_ema20": "20선 위/아래", "px_over_ema60": "60선 위/아래",
    "ema_spread": "이평 간격", "ema_slope": "이평 기울기",
    "rsi": "RSI", "stoch_k": "스토캐스틱", "macd_hist": "MACD 히스토그램",
    "roc_20": "20봉 변화율", "fisher": "피셔 변환",
    "percent_b": "볼린저 %B", "range_position": "구간 내 위치", "cloud_position": "구름 대비",
    "atr_pct": "변동성(ATR)", "bandwidth": "밴드 폭", "squeeze": "스퀴즈",
    "vol_percentile": "변동성 백분위", "volume_ratio": "거래량 배수", "cmf": "자금흐름",
    "trend_state": "추세 상태", "adx": "추세 강도",
    "analog_median": "유사구간 중앙값", "analog_mean": "유사구간 평균",
    "analog_prob_up": "유사구간 상승비율", "analog_spread": "유사구간 흩어짐",
    "analog_corr_max": "가장 닮은 정도", "analog_corr_mean": "평균 닮은 정도",
    "event_recency": "사건 최근성", "event_severity": "사건 크기",
    "event_chart": "차트 사건", "event_macro": "매크로 사건", "event_scheduled": "예정 사건",
    "attention_z": "관심도 급등", "attention_change": "관심도 변화",
    "attention_spike": "관심도 폭발",
    "clv": "종가 위치", "signed_volume": "방향 거래량", "pressure": "매수압력",
    "wick_bias": "꼬리 치우침",
    "market_ret_5": "시장 5봉", "market_ret_20": "시장 20봉",
    "rel_strength_5": "시장 대비 5봉", "rel_strength_20": "시장 대비 20봉",
    "beta": "베타", "market_vol": "시장 변동성",
}


def label(key: str) -> str:
    if key.endswith(REL):
        return f"{LABEL.get(key[:-len(REL)], key[:-len(REL)])} (평소 대비)"
    return LABEL.get(key, key)


def panel(closed: pd.DataFrame, events, *, window: int = 48, horizon: int = 10,
          attention_frame: pd.DataFrame | None = None,
          market_frame: pd.DataFrame | None = None) -> pd.DataFrame:
    """봉마다의 팩터 값. 열은 `CANDIDATES` 중 실제로 만들어진 것들 + `ts`.

    `dataset.build` 를 그대로 부른다 — 인과성 보증이 거기 붙어 있다.
    """
    built = dataset.build(closed, events, window=window, horizon=horizon,
                          attention_frame=attention_frame, market_frame=market_frame)
    if built.empty:
        return pd.DataFrame(columns=["ts", *CANDIDATES])
    have = [c for c in CANDIDATES if c in built.columns]
    out = built[have].copy()
    out.insert(0, "ts", built["ts"].to_numpy())
    return out.replace([np.inf, -np.inf], np.nan)


# 자기 과거와 견주는 창(봉 수). 짧으면 잡음, 길면 레짐 변화를 못 따라간다.
REL_SPAN = 120
# 자기 과거 대비 축의 접미사. `atr_pct__rel` = "이 종목치고 지금 변동성이 높은가".
REL = "__rel"


def relative(values: pd.Series, span: int = REL_SPAN) -> pd.Series:
    """자기 과거 대비 z 점수. **인과적이다** — 봉 i 는 i 까지만 본다.

    이게 왜 필요한가: 원값을 종목끼리 비교하면 "DOGE 는 원래 BTC 보다 많이 움직인다"
    같은 **고정 순위**를 재게 된다. 그 순위는 늘 맞지만 오늘 뭘 볼지는 말해 주지
    않는다. 자기 과거로 나누면 남는 건 "지금 평소와 다른가" 뿐이다.
    """
    column = values.astype("float64")
    mean = column.rolling(span, min_periods=span // 2).mean()
    std = column.rolling(span, min_periods=span // 2).std(ddof=0)
    return ((column - mean) / std.replace(0.0, np.nan)).replace([np.inf, -np.inf], np.nan)


def with_relative(panel: pd.DataFrame, span: int = REL_SPAN) -> pd.DataFrame:
    """한 종목의 팩터 표에 자기 과거 대비 축을 덧붙인다. **종목별로 부를 것** —
    여러 종목을 이어 붙인 표에 그대로 쓰면 종목 경계를 넘어 굴러간다."""
    out = panel.copy()
    for name in panel.columns:
        if name == "ts":
            continue
        out[f"{name}{REL}"] = relative(panel[name], span)
    return out


def all_candidates() -> tuple[str, ...]:
    """원값 + 자기 과거 대비. IC 는 둘 다 재고, 이긴 쪽만 점수에 들어간다."""
    return CANDIDATES + tuple(f"{c}{REL}" for c in CANDIDATES)


def forward(closed: pd.DataFrame, horizon: int) -> pd.Series:
    """봉 i 에서 [i+1, i+horizon] 의 로그수익률. 라벨이다 — 피처로 새면 안 된다."""
    return dataset.forward_return(closed, horizon)
