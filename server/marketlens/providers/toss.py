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
from pathlib import Path
from typing import AsyncIterator

import httpx
import pandas as pd

from . import base
from ..core.candle import Candle, resample, to_frame
from ..core.timeframe import bar_closed, to_ms
from .base import (CandleAggregator, Provider, ProviderError, ProviderInfo,
                   ProviderUnavailable, SymbolCatalog, SymbolNotFound, register)

REST = "https://openapi.tossinvest.com"
# 401(토큰 무효)·429(한도)에 물러설 횟수와 대기.
MAX_RETRIES = 2
RETRY_WAIT = 2.0
WS = "wss://openapi-ws.tossinvest.com/ws/v1"

# 토스가 직접 주는 봉. 나머지는 여기서 접어 만든다.
NATIVE = {"1m": "1m", "1d": "1d"}
DERIVED = {"1w": "1d"}          # 주봉은 일봉을 접는다
# 일봉의 시각을 어느 지역의 하루로 읽을지. 토스는 일봉을 **현지 자정**으로 준다 —
# KST 자정은 UTC 로 전날 15:00 이라, 그냥 UTC 격자로 내리면 한국 일봉이 하루씩 밀린다.
MARKET_TZ = {"kr": "Asia/Seoul", "us": "America/New_York"}
MAX_PER_CALL = 200
# 페이지 사이에 두는 간격. 한도가 문서에 있으니 몰아치지 않는다.
PAGE_PAUSE = 0.12
MAX_PAGES = 40


class TossClient:
    """토큰과 HTTP 를 맡는다. 국내·미국 프로바이더가 이 하나를 나눠 쓴다."""

    def __init__(self) -> None:
        self._token: tuple[str, float] | None = None
        self._lock = asyncio.Lock()

    # --- 토큰을 프로세스끼리 나눠 쓴다 ---
    #
    # 토스는 **클라이언트당 토큰이 하나**다. 새로 발급하면 앞 토큰이 죽는다. 그래서
    # 서버와 학습 스크립트를 같이 돌리면 서로의 토큰을 무효로 만들며 401 을 주고받는다
    # (실제로 국내주식 차트가 통째로 비었다). 파일 한 곳에 두고 나눠 쓰면 그 싸움이
    # 사라진다. `store_data/` 는 gitignore 라 저장소에 안 올라간다.
    @staticmethod
    def _cache_path() -> Path:
        # 저장소 뿌리 기준 절대경로. 상대경로면 서버와 스크립트가 서로 다른 파일을
        # 보게 되어 나눠 쓰는 의미가 없어진다.
        return Path(__file__).resolve().parents[3] / "store_data" / "toss_token.json"

    def _read_cache(self) -> tuple[str, float] | None:
        try:
            body = json.loads(self._cache_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        access, expires = body.get("access"), body.get("expires")
        if not access or not isinstance(expires, (int, float)):
            return None
        return str(access), float(expires)

    def _write_cache(self, access: str, expires: float) -> None:
        path = self._cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"access": access, "expires": expires}),
                            encoding="utf-8")
        except OSError:
            pass                      # 못 써도 동작은 해야 한다. 느려질 뿐이다.

    def forget(self) -> None:
        """죽은 토큰을 버린다. 파일까지 지워야 다음 프로세스도 새로 받는다."""
        self._token = None
        try:
            self._cache_path().unlink(missing_ok=True)
        except OSError:
            pass

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
            # 다른 프로세스가 이미 받아 뒀으면 그걸 쓴다. 새로 받으면 그 토큰이 죽는다.
            shared = self._read_cache()
            if shared and shared[1] > time.time() + 60:
                self._token = shared
                return shared[0]
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
            expires = time.time() + float(body.get("expires_in", 3600))
            self._token = (access, expires)
            self._write_cache(access, expires)
            return access

    async def get(self, path: str, params: dict) -> dict:
        """토스 REST 한 번. 401 과 429 는 **고장이 아니라 상태**라 물러섰다 다시 친다.

        401 이 왜 나는가: 토스는 새 토큰을 내주면 앞 토큰을 무효로 만든다. 서버가
        떠 있는 동안 `scripts/screen.py` 나 작업 스케줄러가 토큰을 새로 받으면,
        서버가 들고 있던 토큰이 그 순간 죽는다. 그때 **버리고 다시 받지 않으면
        서버를 다시 띄울 때까지 국내주식이 통째로 막힌다.**
        """
        for attempt in range(MAX_RETRIES + 1):
            headers = {"Authorization": f"Bearer {await self.token()}"}
            async with httpx.AsyncClient(timeout=20.0) as http:
                try:
                    res = await http.get(f"{REST}{path}", params=params, headers=headers)
                    res.raise_for_status()
                    return res.json()
                except httpx.HTTPStatusError as exc:
                    code = exc.response.status_code
                    if code == 404:
                        raise SymbolNotFound(
                            f"토스에 그 종목이 없다: {params.get('symbol')}") from exc
                    if code == 401 and attempt < MAX_RETRIES:
                        self.forget()               # 죽은 토큰을 버리고 다시 받는다
                        continue
                    if code == 429 and attempt < MAX_RETRIES:
                        await asyncio.sleep(RETRY_WAIT * (attempt + 1))
                        continue
                    if code == 429:
                        raise ProviderError("토스 호출 한도를 넘었다 — 잠시 뒤 다시") from exc
                    raise ProviderError(f"토스 조회 실패 ({code})") from exc
                except httpx.HTTPError as exc:
                    raise ProviderError(f"토스에 연결하지 못했다: {exc}") from exc
        raise ProviderError("토스 조회 실패 — 재시도를 다 썼다")


client = TossClient()


def _to_ms(stamp: str) -> int:
    """ISO 8601(오프셋 포함) → UTC epoch ms."""
    return int(pd.Timestamp(stamp).tz_convert("UTC").timestamp() * 1000)


def _local_day_ms(ts_ms: int, tz: str) -> int:
    """그 시각이 속한 **현지 달력 날짜**를 UTC 자정으로. 일봉 라벨을 여기서 정한다."""
    local = pd.Timestamp(ts_ms, unit="ms", tz="UTC").tz_convert(tz)
    return int(pd.Timestamp(local.date(), tz="UTC").timestamp() * 1000)


class TossProvider(Provider):
    """국내·미국을 각각 하나씩 등록한다.

    사건의 범위(`market:kr` / `market:us`)가 갈려야 이벤트 스터디가 섞이지 않는다.
    """

    def __init__(self, info: ProviderInfo, stream_type: str) -> None:
        self.info = info
        self._stream_type = stream_type      # trade:kr | trade:us
        self._tz = MARKET_TZ[info.market]
        # 토큰은 모듈 전역 `client` 가 공유하지만 목록은 시장마다 다르다 —
        # 국내와 미국을 같은 캐시에 넣으면 서로를 덮는다.
        self._catalog = SymbolCatalog()

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
            daily = interval == "1d"
            rows = [self._row(c, timeframe, now, self._tz if daily else None,
                              self.info.market) for c in page] + rows
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
    def _row(candle: dict, timeframe: str, now: int, tz: str | None = None,
             market: str = "") -> dict:
        # 가격이 문자열로 온다(소수 손실을 막으려는 것). float 로 바꾸는 건 여기 한 번뿐이다.
        ts = _to_ms(candle["timestamp"])
        if tz is not None:
            # 일봉은 **현지 달력의 그날**을 UTC 자정으로 표시한다. 야후도 결과적으로
            # 그렇게 들어오고, 화면에 찍히는 날짜가 사람이 읽는 그 날짜가 된다.
            ts = _local_day_ms(ts, tz)
        return {
            "ts": ts,
            "open": float(candle["openPrice"]),
            "high": float(candle["highPrice"]),
            "low": float(candle["lowPrice"]),
            "close": float(candle["closePrice"]),
            "volume": float(candle.get("volume") or 0.0),
            "closed": bar_closed(ts, timeframe, now, market),
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

    async def catalog(self) -> list[dict]:
        """이 시장의 상장 종목 전부. 코스피만 2,479건이다.

        **응답 모양에 주의.** 실제 토스는 `{"result": [ ... ]}` 로 리스트를 준다.
        예전 코드가 `result["stocks"]` 를 찾아서 `list.get` 으로 늘 터졌다 —
        그래서 토스 종목 검색이 지금까지 한 번도 동작한 적이 없다.
        두 모양을 다 받는다. 스펙이 바뀌어도 조용히 죽지 않게.
        """
        async def build() -> list[dict]:
            found: list[dict] = []
            failures: list[str] = []
            for market in self._markets():
                try:
                    body = await client.get("/api/v1/stocks/all",
                                            {"market": market, "status": "ACTIVE"})
                except ProviderError as exc:
                    failures.append(f"{market}: {exc}")
                    continue
                result = body.get("result")
                rows = result if isinstance(result, list) else (result or {}).get("stocks") or []
                for row in rows:
                    symbol = str(row.get("symbol", ""))
                    if not symbol:
                        continue
                    name = str(row.get("name") or row.get("koreanName") or symbol)
                    found.append(base.item(symbol, name, market,
                                           str(row.get("securityType", ""))))
            if not found:
                raise ProviderError("토스 종목 목록을 못 받았다: " + " / ".join(failures))
            # 보통주를 ETF·리츠 앞에 둔다. "삼성"을 치면 삼성전자보다
            # `RISE 삼성전자SK하이닉스채권혼합50` 같은 상품이 먼저 나온다.
            found.sort(key=lambda x: x["kind"] != "STOCK")
            return base.prefer(found, self.info.default_symbols)

        items, _ = await self._catalog.get(build)
        return items

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
        lists_symbols=True,
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
        lists_symbols=True,
    ),
    stream_type="trade:us",
))
