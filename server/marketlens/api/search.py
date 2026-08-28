"""종목 찾기 — 목록과 검색.

시장을 먼저 고르지 않아도 되게 하는 것이 이 파일의 전부다. "삼성"을 치면 국내주식이,
"BTC"를 치면 바이낸스와 업비트가 같이 나오고, 고르면 프로바이더까지 알아서 바뀐다.

## 실패를 조용히 숨기지 않는다

프로바이더 하나가 늦거나 죽어도 나머지 결과는 나가야 한다. 대신 **왜 빠졌는지를
`sources` 에 적는다** — `/api/events` 가 이미 쓰는 방식 그대로다. 조용히 빠지면
사용자는 "그 시장엔 그 종목이 없다"고 잘못 배운다.
"""
from __future__ import annotations

import asyncio

from ..core.text import with_josa
from ..providers import ProviderError, all_providers, get as get_provider

# 야후 하나가 늦다고 전체를 붙잡지 않는다. 잘린 프로바이더도 그 사실이 남는다.
TIMEOUT = 2.5


async def catalog(provider: str) -> dict:
    """한 시장의 전체 종목.

    목록이 없는 시장(야후·KIS)은 **오류가 아니다.** 실시간이 없는 프로바이더를
    빨간불로 안 그리는 것과 같은 이유다 — 그건 상태지 고장이 아니다.
    """
    found = get_provider(provider)
    if not found.info.lists_symbols:
        return {"provider": provider, "listed": False, "count": 0, "items": [],
                "reason": f"{with_josa(found.info.name, '은는')} 전체 목록을 "
                          f"주지 않는다 — 이름으로 검색만 된다"}
    if not found.available:
        return {"provider": provider, "listed": False, "count": 0, "items": [],
                "reason": found.unavailable_reason}
    try:
        items = await found.catalog()
    except ProviderError as exc:
        return {"provider": provider, "listed": False, "count": 0, "items": [],
                "reason": str(exc)}
    return {"provider": provider, "listed": True, "count": len(items),
            "items": items, "reason": ""}


async def _search_one(provider, query: str) -> tuple[str, list[dict], str]:
    try:
        return provider.info.key, await provider.search(query), ""
    except ProviderError as exc:
        return provider.info.key, [], str(exc)
    except Exception as exc:                                       # noqa: BLE001
        return provider.info.key, [], f"{type(exc).__name__}: {exc}"


def _fold(groups: list[dict]) -> list[dict]:
    """같은 시장에서 같은 심볼이면 한 줄로 접는다.

    `AAPL` 은 야후·미국주식·토스미국 셋에서 온다. 세 줄로 나오면 고르는 사람이
    더 헷갈린다 — 앞선 하나만 남기고 나머지는 `also` 로 붙여, 원하면 그쪽을
    고를 수 있게 한다. 암호화폐는 심볼이 `BTCUSDT` 와 `KRW-BTC` 로 달라 안 접힌다.
    """
    seen: dict[tuple[str, str], dict] = {}
    for group in groups:
        for row in group["items"]:
            key = (group["market"], str(row["symbol"]).upper())
            if key in seen:
                seen[key].setdefault("also", []).append(group["provider"])
                continue
            merged = dict(row)
            merged["provider"] = group["provider"]
            merged["providerName"] = group["name"]
            seen[key] = merged
    out: list[dict] = []
    for group in groups:
        rows = [r for r in seen.values() if r["provider"] == group["provider"]]
        if rows:
            out.append({**{k: v for k, v in group.items() if k != "items"},
                        "items": rows})
    return out


async def search(query: str, provider: str | None = None) -> dict:
    """`provider` 를 주면 그 시장만, 안 주면 **키가 있는 시장 전부**를 동시에."""
    if provider:
        targets = [get_provider(provider)]
    else:
        # 키가 없는 시장은 뺀다 — 골라 봐야 차트가 안 뜬다. 목록 화면에는 남아
        # "키 필요"로 보이므로, 왜 없는지는 거기서 알 수 있다.
        targets = [p for p in all_providers() if p.available]

    tasks = {asyncio.create_task(_search_one(p, query)): p for p in targets}
    done, pending = await asyncio.wait(tasks, timeout=TIMEOUT)
    for task in pending:
        task.cancel()

    results: dict[str, tuple[list[dict], str]] = {}
    for task in done:
        key, items, error = task.result()
        results[key] = (items, error)
    for task in pending:
        results[tasks[task].info.key] = ([], f"{TIMEOUT}초 안에 못 받았다")

    groups, sources = [], {}
    for found in targets:
        key = found.info.key
        items, error = results.get(key, ([], "응답 없음"))
        sources[key] = {"ok": not error, "count": len(items), "error": error}
        if items:
            groups.append({"provider": key, "name": found.info.name,
                           "market": found.info.market, "items": items})
    return {"q": query, "groups": _fold(groups), "sources": sources}
