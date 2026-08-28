"""캔들 캐시.

같은 구간을 두 번 받지 않는다. 거래소 쪽 한도(Binance 분당 가중치, Finnhub 분당 60회)를
아끼려는 것도 있지만, 진짜 이유는 화면을 여러 개 열었을 때 지표가 서로 다른 스냅샷 위에서
계산되는 걸 막기 위해서다.
"""
from __future__ import annotations

import threading
import time

import pandas as pd

# 타임프레임별 유효기간. 봉이 하나 닫히기 전에 다시 받을 이유가 없다.
TTL_SECONDS = {"1m": 20, "3m": 45, "5m": 60, "15m": 120, "30m": 180,
               "1h": 300, "4h": 600, "1d": 900, "1w": 1800}
DEFAULT_TTL = 60
MAX_ENTRIES = 200


class CandleCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[tuple, tuple[float, pd.DataFrame]] = {}

    @staticmethod
    def _ttl(timeframe: str) -> float:
        return TTL_SECONDS.get(timeframe, DEFAULT_TTL)

    def get(self, provider: str, symbol: str, timeframe: str, limit: int) -> pd.DataFrame | None:
        key = (provider, symbol, timeframe)
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            stored_at, df = entry
            if time.time() - stored_at > self._ttl(timeframe):
                self._data.pop(key, None)
                return None
            # 더 긴 구간을 요청했다면 캐시로는 못 답한다.
            if len(df) < limit:
                return None
            return df.tail(limit).reset_index(drop=True).copy()

    def put(self, provider: str, symbol: str, timeframe: str, df: pd.DataFrame) -> None:
        with self._lock:
            if len(self._data) >= MAX_ENTRIES:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                self._data.pop(oldest, None)
            self._data[(provider, symbol, timeframe)] = (time.time(), df.copy())

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


cache = CandleCache()
