"""라벨링.

'다음 봉이 올랐나'로 라벨을 만들면 모델이 배우는 건 노이즈다. +0.01% 상승과 +5% 상승이
같은 정답이 되고, 손절에 걸려 죽는 경로가 정답으로 남는다.

그래서 삼중 장벽(triple barrier)을 쓴다 — 익절선·손절선·시간 만료 중 **먼저 닿는 것**이
정답이다. 실제로 그 자리에서 들어갔을 때 무슨 일이 났는지와 같은 질문이다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ...indicators import _math as m


def triple_barrier(
    df: pd.DataFrame,
    horizon: int = 10,
    take_profit: float = 2.0,
    stop_loss: float = 1.0,
    atr_period: int = 14,
) -> pd.DataFrame:
    """익절/손절 폭은 ATR 배수로 잡는다 — 종목마다 변동성이 다르기 때문.

    돌려주는 것: label(+1/0/-1), hit_bars(몇 봉 만에 닿았나), ret(그때까지의 수익률).
    마지막 `horizon` 봉은 결과를 알 수 없으므로 NaN 이다. 이걸 0으로 메우면
    학습 데이터에 거짓 정답이 섞인다.
    """
    close = df["close"].astype("float64").to_numpy()
    high = df["high"].astype("float64").to_numpy()
    low = df["low"].astype("float64").to_numpy()
    atr = m.atr(df, atr_period).to_numpy()
    size = len(df)

    label = np.full(size, np.nan)
    hit_bars = np.full(size, np.nan)
    ret = np.full(size, np.nan)

    for i in range(size - horizon):
        if not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        entry = close[i]
        upper = entry + take_profit * atr[i]
        lower = entry - stop_loss * atr[i]
        outcome, bars = 0, horizon
        for step in range(1, horizon + 1):
            j = i + step
            # 한 봉 안에서 위아래를 다 건드리면 어느 쪽이 먼저인지 알 수 없다.
            # 손절이 먼저 닿았다고 본다 — 낙관적으로 세면 백테스트가 부풀려진다.
            if low[j] <= lower:
                outcome, bars = -1, step
                break
            if high[j] >= upper:
                outcome, bars = 1, step
                break
        label[i] = outcome
        hit_bars[i] = bars
        ret[i] = close[i + bars] / entry - 1.0

    return pd.DataFrame(
        {"label": label, "hit_bars": hit_bars, "ret": ret}, index=df.index
    )
