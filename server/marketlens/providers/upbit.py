"""Upbit — 원화 마켓. 키 없이 쓴다.

Binance 와 달리 실시간은 체결(trade)만 온다. 봉은 `CandleAggregator` 가 접는다.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
import pandas as pd

from . import base
from ..core.candle import Candle, to_frame
from ..core.timeframe import to_ms
from .base import (CandleAggregator, Provider, ProviderError, ProviderInfo,
                   SymbolCatalog, SymbolNotFound, register)

REST = "https://api.upbit.com/v1"
WS = "wss://api.upbit.com/websocket/v1"

# 분봉은 unit 값으로, 일·주봉은 다른 경로로 간다.
MINUTE_UNITS = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
PATHS = {"1d": "days", "1w": "weeks"}
MAX_PER_CALL = 200


def _parse_utc(text: str) -> int:
    """'2026-08-28T09:30:00' (UTC, 타임존 표기 없음) → epoch ms."""
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp() * 1000)


# 쪽 사이 대기와 429 재시도. 3천봉이면 15쪽이라 몰아치면 바로 한도에 걸린다.
PAGE_PAUSE = 0.12
MAX_RETRIES = 3
RETRY_WAIT = 1.5


class UpbitProvider(Provider):
    info = ProviderInfo(
        key="upbit",
        name="Upbit",
        market="crypto",
        timeframes=tuple(MINUTE_UNITS) + tuple(PATHS),
        requires_key=False,
        note="원화 마켓. 실시간은 체결만 와서 봉은 서버가 접는다",
        default_symbols=("KRW-BTC", "KRW-ETH", "KRW-XRP"),
        lists_symbols=True,
    )

    def __init__(self) -> None:
        self._catalog = SymbolCatalog()

    def _endpoint(self, timeframe: str) -> tuple[str, dict]:
        if timeframe in MINUTE_UNITS:
            return f"{REST}/candles/minutes/{MINUTE_UNITS[timeframe]}", {}
        if timeframe in PATHS:
            return f"{REST}/candles/{PATHS[timeframe]}", {}
        raise ProviderError(f"Upbit 는 {timeframe} 봉을 주지 않는다")

    async def history(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        url, extra = self._endpoint(timeframe)
        step = to_ms(timeframe)
        now = int(time.time() * 1000)
        rows: list[dict] = []
        before: str | None = None
        tries = 0

        # 최신부터 200개씩 거슬러 올라간다. `to` 는 배타적이라 그대로 이어 붙으면 된다.
        async with httpx.AsyncClient(timeout=15.0) as client:
            while len(rows) < limit:
                params = {"market": symbol.upper(),
                          "count": min(MAX_PER_CALL, limit - len(rows)), **extra}
                if before:
                    params["to"] = before
                try:
                    res = await client.get(url, params=params)
                    res.raise_for_status()
                    batch = res.json()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in (400, 404):
                        raise SymbolNotFound(
                            f"Upbit 에 '{symbol.upper()}' 마켓이 없다 (예: KRW-BTC)"
                        ) from exc
                    # 429 는 "잠깐 쉬라"는 말이지 고장이 아니다. 3천봉을 받으려면
                    # 15쪽을 이어 받아야 해서 한도에 잘 걸린다 — 물러섰다 다시 친다.
                    if exc.response.status_code == 429 and tries < MAX_RETRIES:
                        tries += 1
                        await asyncio.sleep(RETRY_WAIT * tries)
                        continue
                    raise ProviderError(
                        f"Upbit 응답 오류 ({exc.response.status_code})"
                    ) from exc
                except httpx.HTTPError as exc:
                    raise ProviderError(f"Upbit 에 연결하지 못했다: {exc}") from exc
                if not batch:
                    break
                tries = 0
                rows = [self._row(c, step, now) for c in reversed(batch)] + rows
                before = batch[-1]["candle_date_time_utc"]
                if len(batch) < params["count"]:
                    break
                # 쪽 사이에 숨을 준다. 업비트는 초당 요청 수를 본다.
                await asyncio.sleep(PAGE_PAUSE)

        return to_frame(rows[-limit:])

    @staticmethod
    def _row(candle: dict, step: int, now: int) -> dict:
        open_ts = _parse_utc(candle["candle_date_time_utc"])
        return {
            "ts": open_ts,
            "open": float(candle["opening_price"]),
            "high": float(candle["high_price"]),
            "low": float(candle["low_price"]),
            "close": float(candle["trade_price"]),
            "volume": float(candle["candle_acc_trade_volume"]),
            "closed": open_ts + step <= now,
        }

    async def stream(self, symbol: str, timeframe: str) -> AsyncIterator[Candle]:
        import websockets

        self._endpoint(timeframe)  # 지원 여부를 먼저 거절한다
        aggregator = CandleAggregator(timeframe)
        request = [
            {"ticket": "market-lens"},
            {"type": "trade", "codes": [symbol.upper()]},
            {"format": "DEFAULT"},
        ]
        async with websockets.connect(WS, ping_interval=20, close_timeout=5) as socket:
            await socket.send(json.dumps(request))
            async for raw in socket:
                # Upbit 는 바이너리 프레임으로 JSON 을 보낸다.
                payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                if payload.get("type") != "trade":
                    continue
                for candle in aggregator.add(
                    int(payload["timestamp"]),
                    float(payload["trade_price"]),
                    float(payload.get("trade_volume", 0.0)),
                ):
                    yield candle

    async def catalog(self) -> list[dict]:
        """업비트 전체 마켓(약 850). KRW 마켓을 먼저 담아 검색 순서를 살린다."""
        async def build() -> list[dict]:
            async with httpx.AsyncClient(timeout=15.0) as client:
                try:
                    res = await client.get(f"{REST}/market/all",
                                           params={"isDetails": "false"})
                    res.raise_for_status()
                except httpx.HTTPError as exc:
                    raise ProviderError(f"Upbit 종목 목록 실패: {exc}") from exc
            rows = sorted(res.json(),
                          key=lambda m: (not m["market"].startswith("KRW-"),
                                         len(m["market"])))
            return base.prefer(
                [base.item(m["market"], m.get("korean_name", ""),
                              m["market"].split("-")[0], "spot") for m in rows],
                self.info.default_symbols)

        found, _ = await self._catalog.get(build)
        return found


register(UpbitProvider())
