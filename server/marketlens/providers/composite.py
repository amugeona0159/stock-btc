"""히스토리와 실시간의 출처가 다른 시장을 하나로 묶는다.

미국주식이 그렇다 — 과거는 Stooq, 지금은 Finnhub. 이 이음매는 여기서만 존재한다.
엔진·API·화면은 프로바이더 하나를 볼 뿐이다.
"""
from __future__ import annotations

from typing import AsyncIterator

import pandas as pd

from ..core.candle import Candle
from .base import Provider, ProviderInfo, register
from .finnhub import FinnhubProvider
from .stooq import StooqProvider


class CompositeProvider(Provider):
    def __init__(self, info: ProviderInfo, history_from: Provider, stream_from: Provider) -> None:
        self.info = info
        self._history = history_from
        self._stream = stream_from

    @property
    def available(self) -> bool:
        # 과거만 있어도 차트는 그려진다. 실시간이 막혀 있으면 그건 스트림을 열 때 걸린다.
        return self._history.available

    @property
    def unavailable_reason(self) -> str:
        return self._history.unavailable_reason

    async def history(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        return await self._history.history(symbol, timeframe, limit)

    async def stream(self, symbol: str, timeframe: str) -> AsyncIterator[Candle]:
        self._stream.check()
        async for candle in self._stream.stream(symbol, timeframe):
            yield candle

    async def search(self, query: str) -> list[dict]:
        # 검색은 종목명이 필요하니 실시간 쪽(Finnhub)이 낫다. 키가 없으면 과거 쪽으로.
        if self._stream.available:
            return await self._stream.search(query)
        return await self._history.search(query)


_history = StooqProvider()
_stream = FinnhubProvider()

register(CompositeProvider(
    ProviderInfo(
        key="us_stock",
        name="미국주식",
        market="us",
        # Stooq 가 대는 일·주봉까지만. 분봉 히스토리는 무료로 오는 데가 없다.
        timeframes=("1d", "1w"),
        requires_key=True,
        note="과거 캔들은 Stooq(키 불필요), 실시간 체결은 Finnhub(무료 키)",
        default_symbols=("AAPL", "MSFT", "NVDA", "TSLA"),
    ),
    history_from=_history,
    stream_from=_stream,
))
