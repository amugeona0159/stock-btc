"""야후 파이낸스 — 키 없이 받는 과거 캔들.

원래 여기 Stooq 를 썼는데, 2026년 들어 CSV 엔드포인트가 봇 차단(자바스크립트 검증 페이지)에
걸려 못 쓰게 됐다. 야후의 chart 엔드포인트는 키 없이 열려 있고 분봉까지 준다.

공식 API 가 아니다. 언젠가 막힐 수 있다는 뜻이고, 그때 이 파일만 갈아끼우면 되도록
프로바이더 계약 밖으로는 아무것도 새지 않게 해 뒀다.

접미사로 시장이 갈린다 — `AAPL`(미국) · `005930.KS`(코스피) · `035720.KQ`(코스닥) ·
`^GSPC`(지수) · `BTC-USD`. 그래서 국내주식도 증권계좌 없이 과거 차트는 볼 수 있다.
"""
from __future__ import annotations

import time

import httpx
import pandas as pd

from ..core.candle import to_frame
from ..core.timeframe import floor_ts, to_ms
from .base import (Provider, ProviderError, ProviderInfo, SymbolNotFound,
                   register)

CHART = "https://query1.finance.yahoo.com/v8/finance/chart"
SEARCH = "https://query1.finance.yahoo.com/v1/finance/search"
# 야후는 브라우저가 아닌 요청을 자주 거절한다. 이 헤더가 없으면 429/403 이 온다.
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; market-lens/0.1)"}

INTERVALS = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
             "1h": "60m", "1d": "1d", "1w": "1wk"}

# 야후가 분봉을 주는 최대 기간. 넘겨 달라고 하면 빈 응답이 온다.
MAX_DAYS = {"1m": 7, "5m": 59, "15m": 59, "30m": 59, "1h": 729, "1d": 20000, "1w": 20000}


class YahooProvider(Provider):
    info = ProviderInfo(
        key="yahoo",
        name="야후 파이낸스 (전 세계)",
        market="us",
        timeframes=tuple(INTERVALS),
        requires_key=False,
        realtime=False,
        note="키 없이 과거 캔들만. 접미사로 시장 지정 — 005930.KS, ^GSPC, BTC-USD",
        default_symbols=("AAPL", "MSFT", "NVDA", "005930.KS", "^GSPC"),
    )

    def _interval(self, timeframe: str) -> str:
        try:
            return INTERVALS[timeframe]
        except KeyError:
            raise ProviderError(f"야후는 {timeframe} 봉을 주지 않는다") from None

    async def history(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        interval = self._interval(timeframe)
        step = to_ms(timeframe)
        now_ms = int(time.time() * 1000)

        # 요청 구간은 넉넉히 잡되 야후의 상한을 넘지 않는다. 장이 안 열리는 날이 있어
        # limit 봉을 받으려면 달력 기준으로는 그보다 길게 요청해야 한다.
        span_days = min(MAX_DAYS[timeframe], max(2, int(limit * step / 86_400_000 * 1.8) + 5))
        params = {
            "interval": interval,
            "period1": int((now_ms - span_days * 86_400_000) / 1000),
            "period2": int(now_ms / 1000),
            "includePrePost": "false",
            "events": "div,split",
        }
        async with httpx.AsyncClient(timeout=20.0, headers=HEADERS,
                                     follow_redirects=True) as client:
            try:
                res = await client.get(f"{CHART}/{symbol}", params=params)
                res.raise_for_status()
                body = res.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (400, 404):
                    raise SymbolNotFound(f"야후에 '{symbol}' 종목이 없다") from exc
                raise ProviderError(f"야후 요청 실패 ({exc.response.status_code})") from exc
            except httpx.HTTPError as exc:
                raise ProviderError(f"야후에 연결하지 못했다: {exc}") from exc
            except ValueError as exc:
                raise ProviderError("야후 응답을 읽지 못했다") from exc

        chart = body.get("chart") or {}
        if chart.get("error"):
            raise ProviderError(f"야후: {chart['error'].get('description', '알 수 없는 오류')}")
        results = chart.get("result") or []
        if not results:
            raise SymbolNotFound(f"야후에 '{symbol}' 캔들이 없다")

        result = results[0]
        stamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        if not stamps or not quote.get("close"):
            raise ProviderError(f"'{symbol}' 의 {timeframe} 캔들이 비어 있다 "
                                "— 기간이 너무 길거나 상장 전일 수 있다")

        rows = []
        for i, stamp in enumerate(stamps):
            values = [quote[k][i] for k in ("open", "high", "low", "close")]
            if any(v is None for v in values):
                continue  # 거래가 없던 구간. 앞뒤 값으로 메우면 없던 봉을 만들어낸다.
            # 야후의 일봉 시각은 장 시작 시각이다. 우리 계약은 봉 시각이 격자에 맞아야 하므로
            # 타임프레임 격자로 내린다.
            ts = floor_ts(int(stamp) * 1000, timeframe)
            rows.append({
                "ts": ts,
                "open": float(values[0]), "high": float(values[1]),
                "low": float(values[2]), "close": float(values[3]),
                "volume": float(quote.get("volume", [0])[i] or 0.0),
                "closed": ts + step <= now_ms,
            })
        if not rows:
            raise ProviderError(f"'{symbol}' 응답에 쓸 수 있는 봉이 없다")
        return to_frame(rows[-limit:])

    async def search(self, query: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
            try:
                res = await client.get(SEARCH, params={"q": query, "quotesCount": 20,
                                                       "newsCount": 0})
                res.raise_for_status()
                body = res.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ProviderError(f"야후 종목 검색 실패: {exc}") from exc
        return [
            {
                "symbol": item["symbol"],
                "label": f"{item.get('shortname') or item.get('longname') or item['symbol']}"
                         f" ({item.get('exchange', '')})",
            }
            for item in body.get("quotes", [])
            if item.get("symbol")
        ][:30]


register(YahooProvider())
