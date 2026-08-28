from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from marketlens.core.candle import to_frame
from marketlens.core.timeframe import to_ms

# 시드를 박아 둔다. 테스트가 실행할 때마다 다른 데이터를 보면 실패가 재현되지 않는다.
SEED = 20260828


def make_candles(count: int = 400, timeframe: str = "1h", start: int = 1_700_000_000_000,
                 seed: int = SEED, closed_tail: bool = True) -> pd.DataFrame:
    """기하 브라운 운동으로 만든 합성 봉. 지표가 계산될 만큼은 움직인다."""
    rng = np.random.default_rng(seed)
    step = to_ms(timeframe)
    base = start - (start % step)

    returns = rng.normal(0.0004, 0.012, count)
    close = 100.0 * np.exp(np.cumsum(returns))
    open_ = np.concatenate([[100.0], close[:-1]])
    wiggle = np.abs(rng.normal(0, 0.006, count)) * close
    high = np.maximum(open_, close) + wiggle
    low = np.minimum(open_, close) - wiggle
    volume = rng.lognormal(6.0, 0.4, count)

    rows = [
        {
            "ts": base + i * step,
            "open": float(open_[i]), "high": float(high[i]),
            "low": float(low[i]), "close": float(close[i]),
            "volume": float(volume[i]),
            "closed": True if closed_tail or i < count - 1 else False,
        }
        for i in range(count)
    ]
    if not closed_tail:
        rows[-1]["closed"] = False
    return to_frame(rows)


@pytest.fixture
def candles() -> pd.DataFrame:
    return make_candles()


@pytest.fixture
def short_candles() -> pd.DataFrame:
    return make_candles(count=120, timeframe="1d")
