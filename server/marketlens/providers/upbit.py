"""Upbit — 원화 마켓. 키 없이 쓴다.

Binance 와 달리 실시간은 체결(trade)만 온다. 봉은 `CandleAggregator` 가 접는다.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx
import pandas as pd

from ..core.candle import Candle, to_frame
from ..core.timeframe import to_ms
from .base import (CandleAggregator, Provider, ProviderError, ProviderInfo,
                   SymbolNotFound, register)

REST = "https://api.upbit.com/v1"
WS = "wss://api.upbit.com/websocket/v1"

# 분봉은 unit 값으로, 일·주봉은 다른 경로로 간다.
MINUTE_UNITS = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
PATHS = {"1d": "days", "1w": "weeks"}
MAX_PER_CALL = 200


def _parse_utc(text: str) -> int:
    """'2026-08-28T09:30:00' (UTC, 타임존 표기 없음) → epoch ms."""
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp() * 1000)


class UpbitProvider(Provider):
    info = ProviderInfo(
        key="upbit",
        name="Upbit",
        market="crypto",
        timeframes=tuple(MINUTE_UNITS) + tuple(PATHS),
        requires_key=False,
        note="원화 마켓. 실시간은 체결만 와서 봉은 서버가 접는다",
        default_symbols=("KRW-BTC", "KRW-ETH", "KRW-XRP"),
    )

    def __init__(self) -> None:
        self._markets: list[dict] | None = None

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
                    raise ProviderError(
                        f"Upbit 응답 오류 ({exc.response.status_code})"
                    ) from exc
                except httpx.HTTPError as exc:
                    raise ProviderError(f"Upbit 에 연결하지 못했다: {exc}") from exc
                if not batch:
                    break
                rows = [self._row(c, step, now) for c in reversed(batch)] + rows
                before = batch[-1]["candle_date_time_utc"]
                if len(batch) < params["count"]:
                    break

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

    async def search(self, query: str) -> list[dict]:
        if self._markets is None:
            async with httpx.AsyncClient(timeout=15.0) as client:
                try:
                    res = await client.get(f"{REST}/market/all", params={"isDetails": "false"})
                    res.raise_for_status()
                except httpx.HTTPError as exc:
                    raise ProviderError(f"Upbit 종목 목록 실패: {exc}") from exc
            self._markets = [
                {"symbol": m["market"], "label": f"{m['korean_name']} ({m['market']})"}
                for m in res.json()
            ]
        needle = query.upper()
        hits = [m for m in self._markets
                if needle in m["symbol"].upper() or query in m["label"]]
        hits.sort(key=lambda m: (not m["symbol"].startswith("KRW-"), len(m["symbol"])))
        return hits[:30]


register(UpbitProvider())
