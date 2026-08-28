"""Stooq — 키 없이 받는 과거 일봉.

Finnhub 무료 티어는 실시간 체결은 열어 두면서 과거 캔들(`/stock/candle`)은 403 으로 막는다.
그래서 미국주식은 히스토리와 실시간의 출처가 다르고, 그 이음매는 `composite.py` 한 곳에만 둔다.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

import httpx
import pandas as pd

from ..core.candle import to_frame
from .base import Provider, ProviderError, ProviderInfo

CSV_URL = "https://stooq.com/q/d/l/"
PERIODS = {"1d": "d", "1w": "w"}


def _to_stooq(symbol: str) -> str:
    """AAPL → aapl.us. 이미 접미사가 붙어 있으면 그대로 둔다."""
    s = symbol.strip().lower()
    return s if "." in s else f"{s}.us"


class StooqProvider(Provider):
    info = ProviderInfo(
        key="stooq",
        name="Stooq (과거 일봉)",
        market="us",
        timeframes=tuple(PERIODS),
        requires_key=False,
        realtime=False,
        note="키 없이 일·주봉만. 실시간은 없다",
        default_symbols=("AAPL", "MSFT", "NVDA"),
    )

    async def history(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        if timeframe not in PERIODS:
            raise ProviderError(f"Stooq 는 {timeframe} 봉을 주지 않는다 (일·주봉만)")
        params = {"s": _to_stooq(symbol), "i": PERIODS[timeframe]}
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            try:
                res = await client.get(CSV_URL, params=params)
                res.raise_for_status()
            except httpx.HTTPError as exc:
                raise ProviderError(f"Stooq 요청 실패: {exc}") from exc

        text = res.text.strip()
        if not text or text.lower().startswith("no data"):
            raise ProviderError(f"Stooq 에 {symbol} 데이터가 없다")

        rows: list[dict] = []
        for record in csv.DictReader(io.StringIO(text)):
            try:
                day = datetime.strptime(record["Date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                rows.append({
                    "ts": int(day.timestamp() * 1000),
                    "open": float(record["Open"]),
                    "high": float(record["High"]),
                    "low": float(record["Low"]),
                    "close": float(record["Close"]),
                    "volume": float(record.get("Volume") or 0.0),
                    # 전부 마감된 과거 봉이다. 오늘 봉은 여기 오지 않는다.
                    "closed": True,
                })
            except (KeyError, ValueError):
                continue  # 헤더가 바뀌거나 빈 줄이 섞인 경우
        if not rows:
            raise ProviderError(f"Stooq 응답을 캔들로 읽지 못했다: {text[:80]}")
        return to_frame(rows[-limit:])


# 목록에 따로 올리지 않는다 - 반쪽짜리라 혼자서는 차트가 안 나온다.
# 화면에 뜨는 미국주식 프로바이더는 둘을 묶은 composite.us_stock 하나뿐이다.
