"""Finnhub 뉴스 - 종목별 뉴스와 암호화폐 카테고리 뉴스.

무료 티어에서 열려 있다. 과거 캔들과 달리 뉴스는 막혀 있지 않다.
다만 과거로 얼마나 거슬러 갈 수 있는지가 제한적이라, 오래된 사건은 GDELT 쪽이 낫다.
"""
from __future__ import annotations

import os

import pandas as pd

from ...providers.base import ProviderError, ProviderUnavailable
from ..schema import Event

REST = "https://finnhub.io/api/v1"

# 제목에 이 말이 들어가면 무게를 올린다. 뉴스는 양이 많아 전부 같은 크기로 두면
# 실적 발표와 잡담이 같은 사건이 된다.
HEAVY_WORDS = (
    "sec", "etf", "ban", "hack", "lawsuit", "bankrupt", "fed", "rate", "inflation",
    "규제", "해킹", "파산", "금리", "승인", "소송",
)


def _token() -> str:
    token = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not token:
        raise ProviderUnavailable("FINNHUB_API_KEY 가 비어 있다 - 뉴스 소스를 건너뛴다")
    return token


def _severity(title: str) -> float:
    lowered = title.lower()
    hits = sum(1 for word in HEAVY_WORDS if word in lowered)
    return min(1.0, 0.3 + 0.2 * hits)


async def company(symbol: str, start_ts: int, end_ts: int) -> list[Event]:
    import httpx

    params = {
        "symbol": symbol.upper(),
        "from": pd.Timestamp(start_ts, unit="ms", tz="UTC").strftime("%Y-%m-%d"),
        "to": pd.Timestamp(end_ts, unit="ms", tz="UTC").strftime("%Y-%m-%d"),
        "token": _token(),
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            res = await client.get(f"{REST}/company-news", params=params)
            res.raise_for_status()
        except Exception as exc:
            raise ProviderError(f"Finnhub 뉴스 요청 실패: {exc}") from exc

    return [
        Event(
            ts=int(item["datetime"]) * 1000,
            kind="company",
            title=str(item.get("headline", ""))[:180],
            source="finnhub",
            scope=f"symbol:{symbol.upper()}",
            severity=_severity(str(item.get("headline", ""))),
            url=item.get("url", ""),
            tags=("news", str(item.get("category", "")).lower() or "general"),
        )
        for item in res.json()
        if item.get("datetime") and item.get("headline")
    ]


async def crypto(start_ts: int, end_ts: int) -> list[Event]:
    import httpx

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            res = await client.get(f"{REST}/news",
                                   params={"category": "crypto", "token": _token()})
            res.raise_for_status()
        except Exception as exc:
            raise ProviderError(f"Finnhub 암호화폐 뉴스 실패: {exc}") from exc

    out = []
    for item in res.json():
        ts = int(item.get("datetime", 0)) * 1000
        if not ts or not (start_ts <= ts <= end_ts):
            continue
        headline = str(item.get("headline", ""))[:180]
        if not headline:
            continue
        out.append(Event(
            ts=ts, kind="news", title=headline, source="finnhub",
            scope="market:crypto", severity=_severity(headline),
            url=item.get("url", ""), tags=("news", "crypto"),
        ))
    return out
