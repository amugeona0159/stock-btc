"""성적표.

승률만 보면 안 된다 - 아홉 번 1% 벌고 한 번 20% 잃는 전략도 승률 90% 다.
손익비와 MDD 를 같이 세는 이유다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 연율화 기준 봉 수. 암호화폐는 24시간이라 주식과 다르지만, 비교용 상수라 하나로 둔다.
BARS_PER_YEAR = 365


def summarize(trades: list, equity: pd.Series) -> dict:
    returns = np.array([t.ret for t in trades], dtype="float64")
    wins = returns[returns > 0]
    losses = returns[returns <= 0]

    gross_profit = float(wins.sum()) if wins.size else 0.0
    gross_loss = float(-losses.sum()) if losses.size else 0.0

    result = {
        "trades": len(trades),
        "winRate": round(float(wins.size / returns.size), 4) if returns.size else 0.0,
        "avgWin": round(float(wins.mean()), 6) if wins.size else 0.0,
        "avgLoss": round(float(losses.mean()), 6) if losses.size else 0.0,
        # 손익비: 이겼을 때 버는 크기 / 졌을 때 잃는 크기.
        "payoff": round(float(wins.mean() / abs(losses.mean())), 4)
                  if wins.size and losses.size and losses.mean() != 0 else None,
        # PF: 번 돈 총합 / 잃은 돈 총합. 1.0 이 본전이다.
        "profitFactor": round(gross_profit / gross_loss, 4) if gross_loss > 0 else None,
        "totalReturn": 0.0,
        "maxDrawdown": 0.0,
        "sharpe": None,
    }

    if len(equity) < 2:
        return result

    values = equity.to_numpy(dtype="float64")
    result["totalReturn"] = round(float(values[-1] - 1.0), 6)

    peak = np.maximum.accumulate(values)
    drawdown = values / peak - 1.0
    result["maxDrawdown"] = round(float(drawdown.min()), 6)

    steps = np.diff(values) / values[:-1]
    if steps.std(ddof=1) > 0:
        result["sharpe"] = round(
            float(steps.mean() / steps.std(ddof=1) * np.sqrt(BARS_PER_YEAR)), 4
        )
    return result
