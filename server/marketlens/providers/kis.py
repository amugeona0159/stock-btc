"""한국투자증권 KIS Open API — 국내주식.

인증이 두 겹이다. REST 조회는 `/oauth2/tokenP` 의 access_token 을, 웹소켓은
`/oauth2/Approval` 의 approval_key 를 쓴다. 둘을 섞어 쓰면 조용히 빈 응답이 온다.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import httpx
import pandas as pd

from ..core.candle import Candle, resample, to_frame
from ..core.timeframe import to_ms
from . import base
from .base import (CandleAggregator, Provider, ProviderError, ProviderInfo,
                   ProviderUnavailable, register)

HOSTS = {
    "prod": ("https://openapi.koreainvestment.com:9443", "ws://ops.koreainvestment.com:21000"),
    "paper": ("https://openapivts.koreainvestment.com:29443", "ws://ops.koreainvestment.com:31000"),
}

KST = timezone(timedelta(hours=9))

# 일·주·월봉은 하나의 TR 에서 기간 구분 코드만 바꾼다.
PERIOD_CODES = {"1d": "D", "1w": "W"}
# 분봉은 별도 TR 이고 1분 단위만 준다. 그보다 굵은 봉은 받아서 접는다.
MINUTE_TFS = ("1m", "3m", "5m", "15m", "30m", "1h")


class KisProvider(Provider):
    info = ProviderInfo(
        key="kis",
        name="국내주식 (한국투자증권)",
        market="kr",
        timeframes=MINUTE_TFS + tuple(PERIOD_CODES),
        requires_key=True,
        note="증권계좌와 앱키가 필요하다. 분봉 히스토리는 당일치만 온다",
        default_symbols=("005930", "000660", "035720"),
    )

    def __init__(self) -> None:
        self._token: tuple[str, float] | None = None   # (access_token, 만료 epoch)
        self._approval: str | None = None

    # --- 설정 -------------------------------------------------------------
    @property
    def _app_key(self) -> str:
        return os.environ.get("KIS_APP_KEY", "").strip()

    @property
    def _app_secret(self) -> str:
        return os.environ.get("KIS_APP_SECRET", "").strip()

    @property
    def _hosts(self) -> tuple[str, str]:
        return HOSTS.get(os.environ.get("KIS_ENV", "prod").strip().lower(), HOSTS["prod"])

    @property
    def available(self) -> bool:
        return bool(self._app_key and self._app_secret)

    @property
    def unavailable_reason(self) -> str:
        return "" if self.available else "KIS_APP_KEY / KIS_APP_SECRET 이 비어 있다 (.env 참고)"

    # --- 인증 -------------------------------------------------------------
    async def _access_token(self) -> str:
        self.check()
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        rest, _ = self._hosts
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.post(f"{rest}/oauth2/tokenP", json={
                    "grant_type": "client_credentials",
                    "appkey": self._app_key,
                    "appsecret": self._app_secret,
                })
                res.raise_for_status()
            except httpx.HTTPError as exc:
                raise ProviderError(f"KIS 토큰 발급 실패: {exc}") from exc
        body = res.json()
        token = body.get("access_token")
        if not token:
            raise ProviderError(f"KIS 토큰 응답에 access_token 이 없다: {body}")
        # 발급은 하루 몇 번으로 제한된다. 만료까지 들고 있는다.
        self._token = (token, time.time() + float(body.get("expires_in", 86400)))
        return token

    async def _approval_key(self) -> str:
        self.check()
        if self._approval:
            return self._approval
        rest, _ = self._hosts
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.post(f"{rest}/oauth2/Approval", json={
                    "grant_type": "client_credentials",
                    "appkey": self._app_key,
                    # 웹소켓 승인키만 필드 이름이 secretkey 다. appsecret 으로 보내면 빈 응답이 온다.
                    "secretkey": self._app_secret,
                })
                res.raise_for_status()
            except httpx.HTTPError as exc:
                raise ProviderError(f"KIS 승인키 발급 실패: {exc}") from exc
        self._approval = res.json().get("approval_key")
        if not self._approval:
            raise ProviderError("KIS 승인키 응답이 비었다")
        return self._approval

    async def _get(self, path: str, tr_id: str, params: dict) -> dict:
        rest, _ = self._hosts
        headers = {
            "authorization": f"Bearer {await self._access_token()}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                res = await client.get(f"{rest}{path}", headers=headers, params=params)
                res.raise_for_status()
            except httpx.HTTPError as exc:
                raise ProviderError(f"KIS 조회 실패({tr_id}): {exc}") from exc
        body = res.json()
        if body.get("rt_cd") not in (None, "0"):
            raise ProviderError(f"KIS 오류({tr_id}): {body.get('msg1')}")
        return body

    # --- 히스토리 ---------------------------------------------------------
    async def history(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        if timeframe in PERIOD_CODES:
            return await self._daily(symbol, timeframe, limit)
        if timeframe in MINUTE_TFS:
            minutes = await self._minutes(symbol, limit * (to_ms(timeframe) // 60_000))
            return resample(minutes, timeframe).tail(limit).reset_index(drop=True)
        raise ProviderError(f"KIS 는 {timeframe} 봉을 주지 않는다")

    async def _daily(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        today = datetime.now(KST)
        span = timedelta(days=limit * (7 if timeframe == "1w" else 1) + 30)
        body = await self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": (today - span).strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": today.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": PERIOD_CODES[timeframe],
                "FID_ORG_ADJ_PRC": "0",   # 0 = 수정주가. 분할·병합으로 차트가 끊기지 않게.
            },
        )
        rows = []
        for item in body.get("output2") or []:
            if not item.get("stck_bsop_date"):
                continue
            day = datetime.strptime(item["stck_bsop_date"], "%Y%m%d").replace(tzinfo=KST)
            rows.append({
                "ts": int(day.timestamp() * 1000),
                "open": float(item["stck_oprc"]),
                "high": float(item["stck_hgpr"]),
                "low": float(item["stck_lwpr"]),
                "close": float(item["stck_clpr"]),
                "volume": float(item.get("acml_vol") or 0.0),
                "closed": True,
            })
        return to_frame(rows[-limit:])

    async def _minutes(self, symbol: str, want: int) -> pd.DataFrame:
        """1분봉. 한 번에 30개씩, 시각을 거슬러 올라가며 당일치를 채운다."""
        rows: list[dict] = []
        cursor = datetime.now(KST).strftime("%H%M%S")
        seen: set[int] = set()
        for _ in range(max(1, min(20, want // 30 + 1))):
            body = await self._get(
                "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
                "FHKST03010200",
                {
                    "FID_ETC_CLS_CODE": "",
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": symbol,
                    "FID_INPUT_HOUR_1": cursor,
                    "FID_PW_DATA_INCU_YN": "N",
                },
            )
            batch = body.get("output2") or []
            if not batch:
                break
            oldest = None
            for item in batch:
                stamp = f"{item['stck_bsop_date']}{item['stck_cntg_hour']}"
                when = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=KST)
                ts = int(when.timestamp() * 1000)
                if ts in seen:
                    continue
                seen.add(ts)
                rows.append({
                    "ts": ts,
                    "open": float(item["stck_oprc"]),
                    "high": float(item["stck_hgpr"]),
                    "low": float(item["stck_lwpr"]),
                    "close": float(item["stck_prpr"]),
                    "volume": float(item.get("cntg_vol") or 0.0),
                    "closed": True,
                })
                oldest = item["stck_cntg_hour"] if oldest is None else min(oldest, item["stck_cntg_hour"])
            if oldest is None or oldest <= "090000":
                break
            cursor = oldest
        if not rows:
            raise ProviderError(f"KIS 분봉이 비었다 ({symbol}) — 장 시작 전이거나 종목코드가 틀렸다")
        return to_frame(rows)

    # --- 실시간 -----------------------------------------------------------
    async def stream(self, symbol: str, timeframe: str) -> AsyncIterator[Candle]:
        import websockets

        _, ws_host = self._hosts
        approval = await self._approval_key()
        aggregator = CandleAggregator(timeframe)
        request = {
            "header": {
                "approval_key": approval,
                "custtype": "P",
                "tr_type": "1",       # 1 = 등록
                "content-type": "utf-8",
            },
            # H0STCNT0 = 국내주식 실시간체결가
            "body": {"input": {"tr_id": "H0STCNT0", "tr_key": symbol}},
        }
        async with websockets.connect(ws_host, ping_interval=20, close_timeout=5) as socket:
            await socket.send(json.dumps(request))
            async for raw in socket:
                text = raw.decode() if isinstance(raw, bytes) else raw
                # 제어 프레임(등록 응답·PINGPONG)은 JSON, 시세는 `0|H0STCNT0|001|필드^필드^...`
                if text.startswith("{"):
                    payload = json.loads(text)
                    if payload.get("header", {}).get("tr_id") == "PINGPONG":
                        await socket.send(text)
                    continue
                parts = text.split("|")
                if len(parts) < 4 or parts[1] != "H0STCNT0":
                    continue
                for record in self._parse_ticks(parts[3]):
                    for candle in aggregator.add(*record):
                        yield candle

    @staticmethod
    def _parse_ticks(body: str) -> list[tuple[int, float, float]]:
        """체결 레코드를 (ts_ms, 가격, 거래량) 으로. 한 프레임에 여러 건이 붙어 온다."""
        fields = body.split("^")
        out: list[tuple[int, float, float]] = []
        # 레코드 하나는 고정 폭이다. 앞 3개만 쓴다 — 종목코드 · 체결시각(HHMMSS) · 현재가.
        width = 46
        today = datetime.now(KST).strftime("%Y%m%d")
        for start in range(0, len(fields) - 2, width):
            chunk = fields[start:start + width]
            if len(chunk) < 14:
                break
            try:
                when = datetime.strptime(f"{today}{chunk[1]}", "%Y%m%d%H%M%S").replace(tzinfo=KST)
                out.append((int(when.timestamp() * 1000), float(chunk[2]), float(chunk[12])))
            except (ValueError, IndexError):
                continue
        return out

    async def search(self, query: str) -> list[dict]:
        # 종목 마스터는 별도 파일 배포라 REST 검색이 없다. 6자리 코드는 그대로 통과시킨다.
        code = query.strip()
        if code.isdigit() and len(code) == 6:
            return [base.item(code, "", "kr", "STOCK")]
        raise ProviderUnavailable("KIS 는 종목명 검색을 제공하지 않는다 — 6자리 종목코드를 넣을 것")


register(KisProvider())
