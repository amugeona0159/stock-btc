"""GDELT — 전세계 뉴스 보도량으로 '이슈가 터진 시점'을 잡는다.

키가 필요 없다. 보도량이 평소보다 크게 튄 시점을 사건으로 본다
(research.library: gdelt_news_volume).

한계를 분명히 해 둘 것: 보도량은 **중요도가 아니라 언론의 관심**이다. 검색어를 어떻게
짜느냐가 결과를 좌우하고, DOC API 는 기본이 최근 3개월이다. 더 과거를 보려면
startdatetime/enddatetime 을 명시해야 한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ...providers.base import ProviderError
from ..schema import Event

API = "https://api.gdeltproject.org/api/v2/doc/doc"

# 시장별 기본 검색어. 화면에서 바꿀 수 있게 밖으로 뺀다.
DEFAULT_QUERIES = {
    "crypto": '(bitcoin OR cryptocurrency OR "crypto market")',
    "us": '("stock market" OR "wall street" OR "S&P 500")',
    "kr": '("korean stocks" OR KOSPI OR "korea market")',
}

# 보도량이 이 백분위를 넘으면 사건으로 본다.
SPIKE_PERCENTILE = 0.97
MIN_POINTS = 40


def _stamp(ts_ms: int) -> str:
    return pd.Timestamp(ts_ms, unit="ms", tz="UTC").strftime("%Y%m%d%H%M%S")


async def timeline(query: str, start_ts: int, end_ts: int) -> pd.DataFrame:
    """보도량 시계열. (ts, value) — value 는 전체 기사 중 비율(%)이다."""
    import httpx

    params = {
        "query": query,
        "mode": "timelinevol",
        "format": "json",
        "startdatetime": _stamp(start_ts),
        "enddatetime": _stamp(end_ts),
    }
    async with httpx.AsyncClient(timeout=12.0) as client:
        try:
            res = await client.get(API, params=params)
            res.raise_for_status()
            body = res.json()
        except Exception as exc:  # httpx 오류와 JSON 파싱 오류를 같이 받는다
            raise ProviderError(f"GDELT 요청 실패: {exc}") from exc

    series = (body.get("timeline") or [{}])[0].get("data", [])
    if not series:
        return pd.DataFrame(columns=["ts", "value"])
    rows = [
        {
            "ts": int(pd.Timestamp(point["date"]).tz_localize("UTC").timestamp() * 1000),
            "value": float(point["value"]),
        }
        for point in series
        if point.get("date")
    ]
    return pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)


async def headlines(query: str, start_ts: int, end_ts: int, limit: int = 20) -> list[dict]:
    """구간의 대표 기사. 사건에 이름을 붙이는 데 쓴다."""
    import httpx

    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": min(250, max(1, limit)),
        "sort": "hybridrel",
        "startdatetime": _stamp(start_ts),
        "enddatetime": _stamp(end_ts),
    }
    async with httpx.AsyncClient(timeout=12.0) as client:
        try:
            res = await client.get(API, params=params)
            res.raise_for_status()
            body = res.json()
        except Exception:
            return []  # 제목은 있으면 좋은 것이지 없으면 못 도는 게 아니다
    return [
        {
            "title": a.get("title", "").strip(),
            "url": a.get("url", ""),
            "domain": a.get("domain", ""),
            "ts": int(pd.Timestamp(a["seendate"]).tz_localize("UTC").timestamp() * 1000)
            if a.get("seendate") else None,
        }
        for a in body.get("articles", [])
        if a.get("title")
    ]


async def spikes(
    query: str, start_ts: int, end_ts: int, scope: str = "global"
) -> list[Event]:
    """보도량이 튄 시점을 사건으로."""
    data = await timeline(query, start_ts, end_ts)
    if len(data) < MIN_POINTS:
        return []

    values = data["value"].to_numpy(dtype="float64")
    threshold = float(np.quantile(values, SPIKE_PERCENTILE))
    baseline = float(np.median(values))
    if threshold <= 0:
        return []

    out: list[Event] = []
    hot = values >= threshold
    # 이어진 봉우리는 한 사건으로 접는다. 안 그러면 사흘짜리 이슈가 사건 세 건이 된다.
    index = 0
    while index < len(values):
        if not hot[index]:
            index += 1
            continue
        end = index
        while end + 1 < len(values) and hot[end + 1]:
            end += 1
        peak = index + int(np.argmax(values[index : end + 1]))
        ratio = values[peak] / baseline if baseline > 0 else np.nan
        out.append(Event(
            ts=int(data["ts"].iloc[peak]),
            kind="news",
            title=f"뉴스 보도량 급증 (평소의 {ratio:.1f}배)" if np.isfinite(ratio)
                  else "뉴스 보도량 급증",
            source="gdelt",
            scope=scope,
            severity=float(np.clip((ratio - 1.0) / 4.0, 0.2, 1.0)) if np.isfinite(ratio) else 0.5,
            tags=("news-volume",),
            note=f"검색어: {query}",
        ))
        index = end + 1
    return out


async def annotate(events: list[Event], query: str, window_hours: int = 12) -> list[Event]:
    """보도량 사건에 대표 기사 제목을 붙인다. 숫자만 있으면 무슨 일인지 알 수 없다."""
    annotated: list[Event] = []
    for event in events:
        span = window_hours * 3_600_000
        articles = await headlines(query, event.ts - span // 2, event.ts + span // 2, limit=5)
        if not articles:
            annotated.append(event)
            continue
        best = articles[0]
        annotated.append(Event(
            ts=event.ts, kind=event.kind, title=best["title"][:180], source=event.source,
            scope=event.scope, severity=event.severity, scheduled=event.scheduled,
            url=best["url"], tags=event.tags + ("headline",),
            note=f"{event.title} · {best['domain']}",
        ))
    return annotated
