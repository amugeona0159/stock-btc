"""예측 3층을 같은 형태로 내보낸다.

규칙(rule) - 통계(stat) - 학습(ml) 은 근거도 신뢰도의 의미도 다르지만, 화면과 백테스트가
셋을 구분하지 않고 다룰 수 있어야 나중에 한 층을 통째로 갈아끼울 수 있다.
"""
from __future__ import annotations

import pandas as pd

from ..signals.engine import evaluate
from . import stat


def combined(
    df: pd.DataFrame,
    timeframe: str = "1d",
    horizon: int = 10,
    model_name: str | None = None,
) -> dict:
    signal = evaluate(df)
    layers = {
        "rule": {
            "available": True,
            "direction": signal.direction,
            "label": signal.label,
            "confidence": round(signal.confidence, 3),
            "reasons": [h.reason for h in signal.hits],
        },
        "stat": stat.project(df, horizon=horizon, timeframe=timeframe),
        "monteCarlo": stat.monte_carlo(df, horizon=horizon),
    }

    if model_name:
        # sklearn 이 없거나 학습 전이면 그 층만 비활성으로 나간다. 나머지 둘은 그대로 쓴다.
        try:
            from .ml import model as ml_model
            layers["ml"] = ml_model.predict(df, model_name)
        except Exception as exc:
            layers["ml"] = {"available": False, "reason": str(exc)}
    else:
        layers["ml"] = {"available": False, "reason": "모델을 지정하지 않았다"}

    return {"horizon": horizon, "timeframe": timeframe, "layers": layers}
