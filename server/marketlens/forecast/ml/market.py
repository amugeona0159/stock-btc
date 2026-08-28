"""시장 요인.

지금까지의 43개 축은 **전부 자기 자신만 본다.** 알트코인이 BTC 를 따라가고 개별주가
지수를 따라가는데, 그 정보가 통째로 빠져 있었다. 인트라데이에서 개별 종목 수익률의
상당 부분은 시장 공통 움직임이라, 시장을 모르면 남는 건 잡음뿐이다.

시장 수익률은 **횡단면 중앙값**으로 잡는다. 시가총액 가중이 더 정확하겠지만 시총을
받아오는 경로가 프로바이더마다 달라 계약이 새고, 중앙값은 한 종목이 튀어도 안 흔들린다.

전부 인과적이다 — 각 봉의 값이 그 봉까지의 정보만 쓴다. as-of 검증이 이걸 확인한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 시장 대비를 재는 창(봉 수). 짧은 것과 긴 것을 같이 본다.
SPANS = (5, 20)
# 베타를 재는 창. 짧으면 잡음, 길면 레짐 변화를 못 따라간다.
BETA_WINDOW = 120

COLUMNS = (
    "market_ret_5", "market_ret_20",
    "rel_strength_5", "rel_strength_20",
    "beta", "market_vol",
)


def market_series(closes: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """종목별 (ts, close) 에서 시장 수익률 계열을 만든다.

    같은 시각에 여러 종목이 있어야 횡단면 중앙값이 의미가 있다 — 한 종목뿐이면
    그 종목이 곧 시장이 되어 상대강도가 항상 0 이 된다.
    """
    frames = []
    for symbol, frame in closes.items():
        close = frame["close"].astype("float64")
        frames.append(pd.DataFrame({
            "ts": frame["ts"].to_numpy(),
            "ret": np.log(close / close.shift(1)).to_numpy(),
            "symbol": symbol,
        }))
    if not frames:
        return pd.DataFrame(columns=["ts", "market_ret"])

    pooled = pd.concat(frames, ignore_index=True).dropna(subset=["ret"])
    grouped = pooled.groupby("ts")["ret"]
    out = pd.DataFrame({
        "ts": grouped.median().index.to_numpy(),
        "market_ret": grouped.median().to_numpy(),
        "breadth": grouped.count().to_numpy(),
    }).sort_values("ts").reset_index(drop=True)
    return out


def features(closed: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    """봉마다의 시장 대비 위치. 전부 무차원이고 [-1, 1] 안이다."""
    out = pd.DataFrame({c: np.nan for c in COLUMNS}, index=closed.index, dtype="float64")
    if market.empty or len(closed) < BETA_WINDOW + max(SPANS) + 5:
        return out

    lookup = market.set_index("ts")["market_ret"]
    aligned = lookup.reindex(pd.Index(closed["ts"].to_numpy())).to_numpy()
    market_ret = pd.Series(aligned, index=closed.index)
    if market_ret.notna().sum() < BETA_WINDOW:
        return out

    close = closed["close"].astype("float64")
    own_ret = np.log(close / close.shift(1))

    for span in SPANS:
        # 시장이 최근 span 봉 동안 얼마나 움직였나.
        market_move = market_ret.rolling(span, min_periods=span).sum()
        own_move = own_ret.rolling(span, min_periods=span).sum()
        out[f"market_ret_{span}"] = np.tanh(market_move / 0.05)
        # 상대강도: 시장을 얼마나 앞섰나. 알파의 대용이다.
        out[f"rel_strength_{span}"] = np.tanh((own_move - market_move) / 0.03)

    # 베타: 자기 수익률을 시장 수익률에 회귀한 기울기(rolling).
    # cov/var 로 직접 구한다 — 창마다 회귀를 돌리면 수만 봉에서 못 끝난다.
    pair = pd.DataFrame({"own": own_ret, "mkt": market_ret})
    cov = pair["own"].rolling(BETA_WINDOW, min_periods=BETA_WINDOW // 2).cov(pair["mkt"])
    var = pair["mkt"].rolling(BETA_WINDOW, min_periods=BETA_WINDOW // 2).var()
    beta = cov / var.replace(0.0, np.nan)
    # 베타 1 을 가운데로 둔다. 시장과 똑같이 움직이면 0.
    out["beta"] = np.tanh((beta - 1.0) / 1.0)

    # 시장 자체의 변동성이 지금 높은가. 자기 변동성과는 다른 축이다.
    market_vol = market_ret.rolling(BETA_WINDOW, min_periods=BETA_WINDOW // 2).std(ddof=0)
    rank = market_vol.rolling(BETA_WINDOW * 2, min_periods=BETA_WINDOW).apply(
        lambda w: float((w[:-1] < w[-1]).mean()) if len(w) > 1 else np.nan, raw=True
    )
    out["market_vol"] = rank * 2.0 - 1.0

    return out.replace([np.inf, -np.inf], np.nan)


def forward(market: pd.DataFrame, closed: pd.DataFrame, horizon: int) -> pd.Series:
    """봉마다 '앞으로 horizon 봉 동안 시장이 얼마나 갔나'.

    **학습 목표를 잔차로 만들 때만 쓴다.** 피처로 쓰면 미래를 보는 것이다 —
    이 함수를 부르는 곳이 하나뿐인지 늘 확인할 것.
    """
    if market.empty:
        return pd.Series(np.nan, index=closed.index, dtype="float64")
    lookup = market.set_index("ts")["market_ret"]
    aligned = pd.Series(
        lookup.reindex(pd.Index(closed["ts"].to_numpy())).to_numpy(), index=closed.index
    )
    # 봉 i 에서 [i+1, i+horizon] 의 누적. rolling 은 뒤를 보므로 그만큼 당겨 온다.
    return aligned.rolling(horizon, min_periods=horizon).sum().shift(-horizon)
