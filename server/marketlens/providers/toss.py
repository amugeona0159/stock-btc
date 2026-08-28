"""토스증권 Open API — 국내(KRX)와 미국 주식을 한 곳에서.

프로바이더 계약이 `history` / `stream` 둘뿐이라 여기 파일 하나로 끝난다. 지표·유사구간·
이벤트·학습·화면은 하나도 안 고쳤다 — 그러라고 만든 계약이다.

KIS 와 견주면 이쪽이 훨씬 단순하다:
- 인증이 **표준 OAuth2 client_credentials** 하나뿐이다. KIS 처럼 REST 토큰과 웹소켓
  승인키를 따로 받지 않는다.
- 국내와 미국이 **같은 엔드포인트**다. 심볼만 다르다(005930 / AAPL).
- 실시간 체결이 JSON 텍스트 프레임으로 온다. KIS 의 `^` 구분 고정폭 문자열이 아니다.

한계도 분명하다:
- **캔들 단위가 `1m` 과 `1d` 뿐이다.** 주봉은 일봉에서 접어 만든다. 5분~1시간봉은
  1분봉을 수백 번 받아야 해서 열지 않았다 — 그건 야후 쪽이 낫다.
- 한 번에 200봉. 깊은 이력은 `nextBefore` 로 페이지를 넘긴다.

스펙: https://openapi.tossinvest.com/openapi-docs/latest/openapi.json
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import AsyncIterator

import httpx
import pandas as pd

from ..core.candle import Candle, resample, to_frame
from ..core.timeframe import to_ms
from .base import (CandleAggregator, Provider, ProviderError, ProviderInfo,
                   ProviderUnavailable, SymbolNotFound, register)

REST = "https://openapi.tossinvest.com"
WS = "wss://openapi-ws.tossinvest.com/ws/v1"

# 토스가 직접 주는 봉. 나머지는 여기서 접어 만든다.
NATIVE = {"1m": "1m", "1d": "1d"}
DERIVED = {"1w": "1d"}          # 주봉은 일봉을 접는다
MAX_PER_CALL = 200
# 페이지 사이에 두는 간격. 한도가 문서에 있으니 몰아치지 않는다.
PAGE_PAUSE = 0.12
MAX_PAGES = 40


class TossClient:
    """토큰과 HTTP 를 맡는다. 국내·미국 프로바이더가 이 하나를 나눠 쓴다."""

    def __init__(self) -> None:
        self._token: tuple[str, float] | None = None
        self._lock = asyncio.Lock()

    @property
    def client_id(self) -> str:
        return os.environ.get("TOSS_CLIENT_ID", "").strip()

    @property
    def client_secret(self) -> str:
        return os.environ.get("TOSS_CLIENT_SECRET", "").strip()

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def token(self) -> str:
        if not self.configured:
            raise ProviderUnavailable(
                "TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 이 비어 있다 — "
                "토스증권 WTS 설정 > Open API 에서 발급한다 (.env 참고)"
            )
        async with self._lock:
            if self._token and self._token[1] > time.time() + 60:
                return self._token[0]
            async with httpx.AsyncClient(timeout=15.0) as http:
                try:
                    res = await http.post(
                        f"{REST}/oauth2/token",
                        data={
                            "grant_type": "client_credentials",
                            "client_id": self.client_id,
                            "client_secret": self.client_secret,
                        },
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                    res.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in (400, 401, 403):
                        raise ProviderUnavailable(
                            "토스 인증에 실패했다 — client_id/secret 과 허용 IP 를 확인할 것"
                        ) from exc
                    raise ProviderError(f"토스 토큰 발급 실패 ({exc.response.status_code})") from exc
                except httpx.HTTPError as exc:
                    raise ProviderError(f"토스에 연결하지 못했다: {exc}") from exc

            body = res.json()
            access = body.get("access_token")
            if not access:
                raise ProviderError(f"토스 토큰 응답에 access_token 이 없다: {body}")
            self._token = (access, time.time() + float(body.get("expires_in", 3600)))
            return access

    async def get(self, path: str, params: dict) -> dict:
        headers = {"Authorization": f"Bearer {await self.token()}"}
        async with httpx.AsyncClient(timeout=20.0) as http:
            try:
                res = await http.get(f"{REST}{path}", params=params, headers=headers)
                res.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    raise SymbolNotFound(f"토스에 그 종목이 없다: {params.get('symbol')}") from exc
                if exc.response.status_code == 429:
                    raise ProviderError("토스 호출 한도를 넘었다 — 잠시 뒤 다시") from exc
                raise ProviderError(f"토스 조회 실패 ({exc.response.status_code})") from exc
            except httpx.HTTPError as exc:
                raise ProviderError(f"토스에 연결하지 못했다: {exc}") from exc
        return res.json()


client = TossClient()


def _to_ms(stamp: str) -> int:
    """ISO 8601(오프셋 포함) → UTC epoch ms."""
    return int(pd.Timestamp(stamp).tz_convert("UTC").timestamp() * 1000)


class TossProvider(Provider):
    """국내·미국을 각각 하나씩 등록한다.

    사건의 범위(`market:kr` / `market:us`)가 갈려야 이벤트 스터디가 섞이지 않는다.
    """

    def __init__(self, info: ProviderInfo, stream_type: str) -> None:
        self.info = info
        self._stream_type = stream_type      # trade:kr | trade:us

    @property
    def available(self) -> bool:
        return client.configured

    @property
    def unavailable_reason(self) -> str:
        return "" if self.available else "TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 이 비어 있다 (.env 참고)"

    def _native(self, timeframe: str) -> str:
        if timeframe in NATIVE:
            return NATIVE[timeframe]
        if timeframe in DERIVED:
            return NATIVE[DERIVED[timeframe]]
        raise ProviderError(
            f"토스는 {timeframe} 봉을 주지 않는다 (1m·1d·1w 만) — "
            "그 사이 봉은 야후 쪽이 낫다"
        )

    async def history(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        interval = self._native(timeframe)
        # 주봉은 일봉을 접어 만든다. 접을 만큼 넉넉히 받는다.
        native_limit = limit * 7 if timeframe in DERIVED else limit
        step = to_ms("1d" if interval == "1d" else "1m")
        now = int(time.time() * 1000)

        rows: list[dict] = []
        before: str | None = None
        for _ in range(MAX_PAGES):
            params = {
                "symbol": symbol,
                "interval": interval,
                "count": min(MAX_PER_CALL, native_limit - len(rows)),
                "adjusted": "true",
            }
            if before:
                params["before"] = before
            body = await client.get("/api/v1/candles", params)
            result = body.get("result") or {}
            page = result.get("candles") or []
            if not page:
                break
            # 응답은 최신부터 온다. 앞에 붙여 시간순으로 쌓는다.
            rows = [self._row(c, step, now) for c in page] + rows
            before = result.get("nextBefore")
            if not before or len(rows) >= native_limit:
                break
            await asyncio.sleep(PAGE_PAUSE)

        if not rows:
            raise SymbolNotFound(f"토스에 '{symbol}' 캔들이 없다")
        frame = to_frame(rows)
        if timeframe in DERIVED:
            frame = resample(frame, timeframe)
        return frame.tail(limit).reset_index(drop=True)

    @staticmethod
    def _row(candle: dict, step: int, now: int) -> dict:
        # 가격이 문자열로 온다(소수 손실을 막으려는 것). float 로 바꾸는 건 여기 한 번뿐이다.
        ts = _to_ms(candle["timestamp"])
        return {
            "ts": ts,
            "open": float(candle["openPrice"]),
            "high": float(candle["highPrice"]),
            "low": float(candle["lowPrice"]),
            "close": float(candle["closePrice"]),
            "volume": float(candle.get("volume") or 0.0),
            "closed": ts + step <= now,
        }

    async def stream(self, symbol: str, timeframe: str) -> AsyncIterator[Candle]:
        import websockets

        self._native(timeframe)                 # 지원 여부를 먼저 거절한다
        access = await client.token()
        aggregator = CandleAggregator(timeframe)
        # 구독은 **선언형 전체 교체**다. 배열 하나가 곧 지금의 구독 전부고,
        # subscribe/unsubscribe 액션이 따로 없다.
        declare = json.dumps([{"type": self._stream_type, "codes": [symbol]}])

        async with websockets.connect(
            WS, additional_headers={"Authorization": f"Bearer {access}"},
            ping_interval=20, close_timeout=5,
        ) as socket:
            await socket.send(declare)
            async for raw in socket:
                text = raw.decode() if isinstance(raw, bytes) else raw
                try:
                    payload = json.loads(text)
                except ValueError:
                    continue
                if payload.get("type") != "message":
                    continue                     # ack·에러·pong 프레임
                data = payload.get("data") or {}
                if not data.get("timestamp"):
                    continue
                for candle in aggregator.add(
                    _to_ms(data["timestamp"]),
                    float(data["price"]),
                    float(data.get("volume") or 0.0),
                ):
                    yield candle

    async def search(self, query: str) -> list[dict]:
        needle = query.strip().upper()
        found: list[dict] = []
        for market in self._markets():
            try:
                body = await client.get("/api/v1/stocks/all",
                                        {"market": market, "status": "ACTIVE"})
            except ProviderError:
                continue
            for item in (body.get("result") or {}).get("stocks", []):
                symbol = str(item.get("symbol", ""))
                name = str(item.get("name") or item.get("koreanName") or symbol)
                if needle in symbol.upper() or needle in name.upper() or query in name:
                    found.append({"symbol": symbol, "label": f"{name} ({symbol})"})
            if len(found) >= 30:
                break
        return found[:30]

    def _markets(self) -> tuple[str, ...]:
        return (("KOSPI", "KOSDAQ") if self.info.market == "kr"
                else ("NASDAQ", "NYSE", "AMEX"))


register(TossProvider(
    ProviderInfo(
        key="toss_kr",
        name="국내주식 (토스증권)",
        market="kr",
        timeframes=("1m", "1d", "1w"),
        requires_key=True,
        note="토스증권 계좌 + Open API 키. 실시간 체결과 과거 캔들을 모두 준다",
        default_symbols=("005930", "000660", "035720", "005380"),
    ),
    stream_type="trade:kr",
))

register(TossProvider(
    ProviderInfo(
        key="toss_us",
        name="미국주식 (토스증권)",
        market="us",
        timeframes=("1m", "1d", "1w"),
        requires_key=True,
        note="같은 키로 미국주식까지. Finnhub 처럼 과거 캔들이 막혀 있지 않다",
        default_symbols=("AAPL", "MSFT", "NVDA", "TSLA"),
    ),
    stream_type="trade:us",
))
