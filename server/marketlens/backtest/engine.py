"""백테스트.

규칙은 하나다 — **봉 i 의 판단으로 봉 i+1 의 시가에 체결한다.** 같은 봉의 종가에
체결하면 그 종가를 보고 판단한 셈이 되고, 승률이 실전과 갈라진다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from ..core.candle import closed_only
from ..signals.engine import evaluate
from .metrics import summarize

# 기본 비용. 암호화폐 현물 기준으로 잡았다 — 시장이 다르면 넘겨서 바꾼다.
DEFAULT_FEE = 0.0005      # 편도 0.05%
DEFAULT_SLIPPAGE = 0.0005  # 편도 0.05%


@dataclass
class Trade:
    entry_ts: int
    exit_ts: int
    direction: int
    entry: float
    exit: float
    ret: float
    bars: int

    def to_dict(self) -> dict:
        return {
            "entryTs": self.entry_ts,
            "exitTs": self.exit_ts,
            "direction": self.direction,
            "entry": round(self.entry, 8),
            "exit": round(self.exit, 8),
            "ret": round(self.ret, 6),
            "bars": self.bars,
        }


@dataclass
class Result:
    trades: list[Trade] = field(default_factory=list)
    equity: pd.Series = field(default_factory=lambda: pd.Series(dtype="float64"))
    positions: pd.Series = field(default_factory=lambda: pd.Series(dtype="int64"))
    buy_hold: float = 0.0

    def to_dict(self, include_equity: bool = True) -> dict:
        payload = {
            "trades": [t.to_dict() for t in self.trades],
            "buyHold": round(self.buy_hold, 6),
            "metrics": summarize(self.trades, self.equity),
        }
        if include_equity and len(self.equity):
            payload["equity"] = [
                {"time": int(ts // 1000), "value": float(v)}
                for ts, v in self.equity.items()
            ]
        return payload


Strategy = Callable[[pd.DataFrame], int]


def signal_strategy(threshold: float = 0.15) -> Strategy:
    """기본 전략 — 시그널 엔진의 판단을 그대로 포지션으로."""

    def decide(window: pd.DataFrame) -> int:
        return evaluate(window, threshold).direction

    return decide


def run(
    df: pd.DataFrame,
    strategy: Strategy | None = None,
    warmup: int = 120,
    fee: float = DEFAULT_FEE,
    slippage: float = DEFAULT_SLIPPAGE,
    allow_short: bool = True,
) -> Result:
    closed = closed_only(df).reset_index(drop=True)
    if len(closed) <= warmup + 2:
        return Result()

    decide = strategy or signal_strategy()
    open_px = closed["open"].to_numpy(dtype="float64")
    close_px = closed["close"].to_numpy(dtype="float64")
    ts = closed["ts"].to_numpy()

    positions = np.zeros(len(closed), dtype="int64")
    equity = np.ones(len(closed), dtype="float64")
    trades: list[Trade] = []

    position = 0
    entry_price = 0.0
    entry_index = 0
    cost = fee + slippage

    for i in range(warmup, len(closed) - 1):
        # 봉 i 까지만 보고 정한 뒤, 체결은 봉 i+1 의 시가에서.
        wanted = decide(closed.iloc[: i + 1])
        if not allow_short and wanted < 0:
            wanted = 0

        fill = open_px[i + 1]
        if wanted != position:
            if position != 0:
                gross = (fill / entry_price - 1.0) * position
                net = gross - 2 * cost
                trades.append(Trade(int(ts[entry_index]), int(ts[i + 1]), position,
                                    entry_price, fill, net, i + 1 - entry_index))
            if wanted != 0:
                entry_price, entry_index = fill, i + 1
            position = wanted

        positions[i + 1] = position
        # 자산곡선은 보유 중인 포지션의 봉 수익률을 그대로 누적한다.
        bar_return = (close_px[i + 1] / close_px[i] - 1.0) * position
        equity[i + 1] = equity[i] * (1.0 + bar_return)

    if position != 0:
        fill = close_px[-1]
        gross = (fill / entry_price - 1.0) * position
        trades.append(Trade(int(ts[entry_index]), int(ts[-1]), position,
                            entry_price, fill, gross - 2 * cost, len(closed) - 1 - entry_index))

    equity_series = pd.Series(equity, index=pd.Index(ts, name="ts"))
    return Result(
        trades=trades,
        equity=equity_series,
        positions=pd.Series(positions, index=closed.index),
        buy_hold=float(close_px[-1] / close_px[warmup] - 1.0),
    )
