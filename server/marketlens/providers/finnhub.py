"""Finnhub — 미국주식 실시간 체결.

무료 티어에서 웹소켓 체결은 열려 있고(동시 50종목) 과거 캔들은 403 이다.
그래서 이 프로바이더는 실시간만 맡고, 히스토리는 Stooq 가 댄다 — `composite.py` 참고.
"""
from __future__ import annotations

import json
import os
from typing import AsyncIterator

import httpx
import pandas as pd

from ..core.candle import Candle
from .base import (CandleAggregator, Provider, ProviderError, ProviderInfo,
                   ProviderUnavailable)

REST = "https://finnhub.io/api/v1"
WS = "wss://ws.finnhub.io"


class FinnhubProvider(Provider):
    info = ProviderInfo(
        key="finnhub",
        name="Finnhub (미국 실시간)",
        market="us",
        timeframes=("1m", "3m", "5m", "15m", "30m", "1h"),
        requires_key=True,
        note="무료 키로 실시간 체결만. 과거 캔들은 유료라 Stooq 가 대신한다",
        default_symbols=("AAPL", "MSFT", "NVDA"),
    )

    @property
    def _token(self) -> str:
        return os.environ.get("FINNHUB_API_KEY", "").strip()

    @property
    def available(self) -> bool:
        return bool(self._token)

    @property
    def unavailable_reason(self) -> str:
        return "" if self.available else "FINNHUB_API_KEY 가 비어 있다 (.env 참고)"

    async def history(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        raise ProviderUnavailable(
            "Finnhub 무료 티어는 과거 캔들을 주지 않는다 — us_stock 프로바이더를 쓸 것"
        )

    async def stream(self, symbol: str, timeframe: str) -> AsyncIterator[Candle]:
        import websockets

        self.check()
        aggregator = CandleAggregator(timeframe)
        async with websockets.connect(f"{WS}?token={self._token}",
                                      ping_interval=20, close_timeout=5) as socket:
            await socket.send(json.dumps({"type": "subscribe", "symbol": symbol.upper()}))
            async for raw in socket:
                payload = json.loads(raw)
                if payload.get("type") != "trade":
                    continue
                for trade in payload.get("data") or []:
                    for candle in aggregator.add(
                        int(trade["t"]), float(trade["p"]), float(trade.get("v", 0.0))
                    ):
                        yield candle

    async def quote(self, symbol: str) -> dict:
        """현재가 한 점. 장 마감 뒤에는 체결이 안 오므로 마지막 봉을 이걸로 메운다."""
        self.check()
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(f"{REST}/quote",
                                       params={"symbol": symbol.upper(), "token": self._token})
                res.raise_for_status()
            except httpx.HTTPError as exc:
                raise ProviderError(f"Finnhub 시세 요청 실패: {exc}") from exc
        return res.json()

    async def search(self, query: str) -> list[dict]:
        self.check()
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.get(f"{REST}/search",
                                       params={"q": query, "token": self._token})
                res.raise_for_status()
            except httpx.HTTPError as exc:
                raise ProviderError(f"Finnhub 종목 검색 실패: {exc}") from exc
        return [
            {"symbol": item["symbol"], "label": f"{item['description']} ({item['symbol']})"}
            for item in res.json().get("result", [])[:30]
        ]


# 목록에 따로 올리지 않는다 - 반쪽짜리라 혼자서는 차트가 안 나온다.
# 화면에 뜨는 미국주식 프로바이더는 둘을 묶은 composite.us_stock 하나뿐이다.
