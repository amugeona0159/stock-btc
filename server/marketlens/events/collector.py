"""여러 갈래의 사건을 하나로 모은다.

한 소스가 죽어도(키가 없거나 API 가 막혀도) 나머지는 그대로 나간다. 뉴스 API 하나
때문에 "이슈가 있었을 때 어떻게 움직였나" 를 통째로 못 보는 일은 없어야 한다.
"""
from __future__ import annotations

import asyncio
import logging

import pandas as pd

from ..core.candle import closed_only
from . import catalog, detectors, store
from .schema import Event, EventSet
from .sources import fred, gdelt
from .sources import finnhub_news

log = logging.getLogger("marketlens.events")

ALL_SOURCES = ("builtin", "detector", "user", "gdelt", "fred", "finnhub")
# 네트워크 없이도 도는 것들.
OFFLINE_SOURCES = ("builtin", "detector", "user")
# 기본값. FRED 는 키가 필요 없고 빨라서 넣는다 - 금리·물가 사건이 여기서 나온다.
# GDELT 는 느리고 막히는 데가 있어서, Finnhub 뉴스는 키가 필요해서 옵트인이다.
DEFAULT_SOURCES = OFFLINE_SOURCES + ("fred",)


async def collect(
    df: pd.DataFrame,
    symbol: str,
    market: str,
    sources: tuple[str, ...] = DEFAULT_SOURCES,
    gdelt_query: str | None = None,
) -> tuple[EventSet, dict]:
    """구간에 걸리는 사건 전부. (사건 모음, 소스별 상태) 를 돌려준다."""
    closed = closed_only(df).reset_index(drop=True)
    if closed.empty:
        return EventSet(), {}

    start_ts = int(closed["ts"].iloc[0])
    end_ts = int(closed["ts"].iloc[-1])
    collected = EventSet()
    status: dict[str, dict] = {}

    def note(name: str, count: int, error: str = "") -> None:
        status[name] = {"count": count, "ok": not error, "error": error}

    def clip(found: list[Event]) -> list[Event]:
        """차트 구간 밖의 사건은 버린다.

        소스가 범위를 안 지키는 일이 실제로 있다 - FRED 는 월별 시리즈에서 날짜
        파라미터를 무시하고 1947년부터 전부 돌려준다. 한 소스의 그런 사고가
        사건 목록과 이벤트 스터디를 통째로 오염시키면 안 된다.
        """
        return [e for e in found if start_ts <= e.ts <= end_ts]

    if "builtin" in sources:
        found = clip(catalog.builtin(start_ts, end_ts))
        collected.add(*found)
        note("builtin", len(found))

    if "detector" in sources:
        found = clip(detectors.detect(closed, symbol))
        collected.add(*found)
        note("detector", len(found))

    if "user" in sources:
        found = clip(store.between(start_ts, end_ts))
        collected.add(*found)
        note("user", len(found))

    remote: list[tuple[str, asyncio.Future]] = []
    if "gdelt" in sources:
        query = gdelt_query or gdelt.DEFAULT_QUERIES.get(market, gdelt.DEFAULT_QUERIES["crypto"])
        scope = f"market:{market}"
        remote.append(("gdelt", asyncio.ensure_future(gdelt.spikes(query, start_ts, end_ts, scope))))
    if "fred" in sources:
        remote.append(("fred", asyncio.ensure_future(fred.collect(start_ts, end_ts))))
    if "finnhub" in sources:
        task = finnhub_news.crypto(start_ts, end_ts) if market == "crypto" \
            else finnhub_news.company(symbol, start_ts, end_ts)
        remote.append(("finnhub", asyncio.ensure_future(task)))

    for name, task in remote:
        try:
            found = clip(await task)
            collected.add(*found)
            note(name, len(found))
        except Exception as exc:  # noqa: BLE001 - 소스 하나의 실패로 전체를 멈추지 않는다
            log.info("이벤트 소스 %s 건너뜀: %s", name, exc)
            note(name, 0, str(exc))

    return collected, status


def relevant(events: EventSet, symbol: str, market: str) -> list[Event]:
    return events.for_symbol(symbol, market)
