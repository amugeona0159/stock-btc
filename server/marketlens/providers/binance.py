"""Binance — 키가 필요 없는 개발 기준선.

파이프라인 전체(히스토리 → 지표 → 실시간 갱신)를 여기서 완성한 뒤 나머지 프로바이더를
같은 인터페이스로 갈아끼운다. 키 발급을 기다리는 동안 화면이 비어 있으면 아무것도 못 고친다.
"""
from __future__ import annotations

import json
import time
from typing import AsyncIterator

import httpx
import pandas as pd

from . import base
from ..core.candle import Candle, to_frame
from ..core.timeframe import bar_closed, to_ms
from .base import (Provider, ProviderError, ProviderInfo, SymbolCatalog,
                   SymbolNotFound,
                   register)

REST = "https://api.binance.com"
WS = "wss://stream.binance.com:9443/ws"

# Binance 의 interval 문자열은 우리 것과 같은 표기를 쓴다. 그래도 표를 명시해 둔다 —
# 지원 여부를 여기서 거절해야 사용자가 15초짜리 봉을 요청하고 빈 화면을 보지 않는다.
INTERVALS = {tf: tf for tf in ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w")}

MAX_PER_CALL = 1000


class BinanceProvider(Provider):
    info = ProviderInfo(
        key="binance",
        name="Binance",
        market="crypto",
        timeframes=tuple(INTERVALS),
        requires_key=False,
        note="키 없이 실시간 체결과 과거 캔들을 모두 받는다",
        default_symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
        lists_symbols=True,
    )

    def __init__(self) -> None:
        self._catalog = SymbolCatalog()

    def _interval(self, timeframe: str) -> str:
        try:
            return INTERVALS[timeframe]
        except KeyError:
            raise ProviderError(f"Binance 는 {timeframe} 봉을 주지 않는다") from None

    async def history(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        interval = self._interval(timeframe)
        step = to_ms(timeframe)
        now = int(time.time() * 1000)
        rows: list[dict] = []
        end: int | None = None

        # 한 번에 1000봉까지만 준다. 뒤에서 앞으로 거슬러 채운다.
        async with httpx.AsyncClient(timeout=15.0) as client:
            while len(rows) < limit:
                want = min(MAX_PER_CALL, limit - len(rows))
                params = {"symbol": symbol.upper(), "interval": interval, "limit": want}
                if end is not None:
                    params["endTime"] = end
                try:
                    res = await client.get(f"{REST}/api/v3/klines", params=params)
                    res.raise_for_status()
                    batch = res.json()
                except httpx.HTTPStatusError as exc:
                    # 400 은 대개 없는 심볼이다. 사용자에게 502 를 보여줄 이유가 없다.
                    if exc.response.status_code in (400, 404):
                        raise SymbolNotFound(
                            f"Binance 에 '{symbol.upper()}' 종목이 없다"
                        ) from exc
                    raise ProviderError(
                        f"Binance 응답 오류 ({exc.response.status_code})"
                    ) from exc
                except httpx.HTTPError as exc:
                    raise ProviderError(f"Binance 에 연결하지 못했다: {exc}") from exc
                if not batch:
                    break
                rows = [self._row(k, timeframe, now, self.info.market) for k in batch] + rows
                end = int(batch[0][0]) - 1
                if len(batch) < want:
                    break

        return to_frame(rows[-limit:])

    @staticmethod
    def _row(kline: list, timeframe: str, now: int, market: str = "") -> dict:
        open_ts = int(kline[0])
        return {
            "ts": open_ts,
            "open": float(kline[1]),
            "high": float(kline[2]),
            "low": float(kline[3]),
            "close": float(kline[4]),
            "volume": float(kline[5]),
            # 마지막 봉은 아직 자라는 중일 수 있다. 종료 시각이 지났는지로만 판단한다.
            "closed": bar_closed(open_ts, timeframe, now, market),
        }

    async def stream(self, symbol: str, timeframe: str) -> AsyncIterator[Candle]:
        import websockets

        interval = self._interval(timeframe)
        url = f"{WS}/{symbol.lower()}@kline_{interval}"
        async with websockets.connect(url, ping_interval=20, close_timeout=5) as socket:
            async for raw in socket:
                payload = json.loads(raw)
                k = payload.get("k")
                if not k:
                    continue
                # Binance 는 봉을 완성해서 준다. 접는 일(CandleAggregator)이 필요 없는
                # 유일한 프로바이더다.
                yield Candle(
                    ts=int(k["t"]),
                    open=float(k["o"]),
                    high=float(k["h"]),
                    low=float(k["l"]),
                    close=float(k["c"]),
                    volume=float(k["v"]),
                    closed=bool(k["x"]),
                )

    async def catalog(self) -> list[dict]:
        """거래중인 현물 전부(약 500). USDT 쌍을 먼저 담아 검색 순서를 살린다."""
        async def build() -> list[dict]:
            async with httpx.AsyncClient(timeout=20.0) as client:
                try:
                    res = await client.get(f"{REST}/api/v3/exchangeInfo",
                                           params={"permissions": "SPOT"})
                    res.raise_for_status()
                except httpx.HTTPError as exc:
                    raise ProviderError(f"Binance 종목 목록을 못 받았다: {exc}") from exc
            rows = [s for s in res.json().get("symbols", []) if s.get("status") == "TRADING"]
            rows.sort(key=lambda s: (s["quoteAsset"] != "USDT", len(s["symbol"])))
            return base.prefer(
                [base.item(s["symbol"], f"{s['baseAsset']}/{s['quoteAsset']}",
                              s["quoteAsset"], "spot") for s in rows],
                self.info.default_symbols)

        found, _ = await self._catalog.get(build)
        return found


register(BinanceProvider())
