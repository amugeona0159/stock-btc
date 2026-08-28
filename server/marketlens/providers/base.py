"""프로바이더 계약.

메서드는 둘뿐이다 — `history` 와 `stream`. 시장별 분기(`if market == "kr"`)를 엔진이나
API 나 화면에 흘리지 않기 위해서다. 거래소가 ms 를 주든 KST 문자열을 주든,
그 차이는 이 계층 안에서 끝난다.
"""
from __future__ import annotations

import abc
import asyncio
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

import pandas as pd

from ..core.candle import Candle, empty_frame
from ..core.timeframe import floor_ts


# 검색이 한 번에 돌려주는 최대 건수. 목록 화면은 이 컷을 안 쓴다 — 거기는 전부 받는다.
SEARCH_LIMIT = 30


def item(symbol: str, name: str = "", market: str = "", kind: str = "") -> dict:
    """목록·검색 항목 한 벌. **라벨 모양을 정하는 곳은 여기 하나다.**

    지금까지 프로바이더마다 제각각으로 만들었다 — 어디는 `BTC/USDT`, 어디는
    `삼성전자 (005930)`. 한 화면에 섞이면 그게 그대로 보인다.
    """
    label = f"{name} ({symbol})" if name and name != symbol else symbol
    return {"symbol": symbol, "label": label, "name": name or symbol,
            "market": market, "kind": kind}


def prefer(items: list[dict], first: tuple[str, ...]) -> list[dict]:
    """자주 보는 종목을 목록 앞으로. `match` 가 동률에서 이 순서를 그대로 쓴다.

    한글 종목명에는 우선순위를 매길 실마리가 없다 — "삼성"을 치면 삼성화재·삼성제약·
    삼성전자가 다 같은 등급으로 걸리고, 그다음은 종목코드 순서라 삼성전자가 네 번째로
    간다. 시가총액을 받아 오는 경로는 프로바이더마다 다르니, 이 프로그램이 실제로
    추적하는 종목(`default_symbols`)을 앞에 두는 것으로 대신한다.
    """
    if not first:
        return items
    rank = {s.upper(): i for i, s in enumerate(first)}
    return sorted(items, key=lambda x: rank.get(str(x.get("symbol", "")).upper(),
                                                len(rank)))


def match(items: list[dict], query: str, limit: int = SEARCH_LIMIT) -> list[dict]:
    """전체 목록에서 검색어에 맞는 것을 고른다.

    순서는 **완전일치 → 앞부분 일치 → 부분 일치**. 같은 등급이면 **목록이 담아 온
    순서**를 그대로 쓴다 — 거기에 이미 그 시장의 우선순위가 들어 있다(바이낸스는
    USDT 쌍 먼저, 업비트는 KRW 마켓 먼저). 길이로 다시 줄 세우면 `BTC` 를 쳤을 때
    `BTCUSDT` 대신 `BTCTRY` 가 위로 올라온다 — 실제로 그랬다.

    한글은 대소문자 변환을 하면 안 되므로 원문으로도 한 번 본다.
    """
    needle = query.strip()
    if not needle:
        return items[:limit]
    upper = needle.upper()
    scored: list[tuple[int, int, dict]] = []
    for order, entry in enumerate(items):
        symbol = str(entry.get("symbol", "")).upper()
        name = str(entry.get("name", ""))
        haystack = f"{symbol} {name.upper()}"
        # `KRW-BTC` 의 `BTC`, `BTC/USDT` 의 `USDT` 처럼 구분자 뒤도 앞부분으로 친다.
        # 안 그러면 "BTC" 를 쳤을 때 BTC 마켓(BTC-WOM…)만 걸리고 정작 비트코인이
        # 부분일치로 밀린다 — 실제로 그랬다.
        parts = symbol.replace("/", "-").split("-")
        if upper == symbol or upper in parts:
            grade = 0
        elif (symbol.startswith(upper) or name.upper().startswith(upper)
              or any(p.startswith(upper) for p in parts)):
            grade = 1
        elif upper in haystack or needle in name:
            grade = 2
        else:
            continue
        scored.append((grade, order, entry))
    scored.sort(key=lambda row: row[:2])
    return [row[2] for row in scored[:limit]]


class ProviderError(RuntimeError):
    """프로바이더가 데이터를 못 가져왔다. API 는 이걸 502 로 바꾼다."""


class ProviderUnavailable(ProviderError):
    """키가 없거나 설정이 빠져 쓸 수 없다. 앱은 죽지 않고 이 프로바이더만 꺼진다."""


class SymbolNotFound(ProviderError):
    """그런 종목이 없다. 사용자 잘못이지 서버 잘못이 아니므로 502 로 나가면 안 된다."""


@dataclass(frozen=True)
class ProviderInfo:
    key: str
    name: str
    market: str                       # crypto | us | kr
    timeframes: tuple[str, ...]
    requires_key: bool = False
    realtime: bool = True
    note: str = ""
    default_symbols: tuple[str, ...] = ()
    # 전체 종목 목록을 줄 수 있나. 야후·KIS 처럼 못 주는 곳은 '검색만 되는 시장'이고,
    # 그건 오류가 아니라 상태다 — 화면이 목록을 부르기 전에 이걸 보고 말해 준다.
    lists_symbols: bool = False


class Provider(abc.ABC):
    info: ProviderInfo

    @property
    def available(self) -> bool:
        """키나 설정이 갖춰졌는지. False 여도 목록에는 뜨고, 쓰려 할 때만 막힌다."""
        return True

    @property
    def unavailable_reason(self) -> str:
        return ""

    def check(self) -> None:
        if not self.available:
            raise ProviderUnavailable(f"{self.info.name}: {self.unavailable_reason}")

    def supports(self, timeframe: str) -> bool:
        return timeframe in self.info.timeframes

    @abc.abstractmethod
    async def history(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        """표준 캔들. 마지막 봉이 아직 진행 중이면 closed=False 로 준다."""

    async def stream(self, symbol: str, timeframe: str) -> AsyncIterator[Candle]:
        """봉이 갱신될 때마다 하나씩. 실시간이 없는 프로바이더는 그대로 둔다."""
        raise ProviderUnavailable(f"{self.info.name} 은 실시간을 지원하지 않는다")
        yield  # pragma: no cover - 위에서 항상 터진다

    async def catalog(self) -> list[dict]:
        """이 시장의 전체 종목. 빈 리스트면 '검색만 되는 시장'이라는 뜻이다."""
        return []

    async def search(self, query: str) -> list[dict]:
        """기본 구현 — 전체 목록에서 걸러 낸다.

        목록이 있는 프로바이더는 이걸 그대로 쓴다. 목록 코드를 두 벌 두면
        검색 결과와 목록 화면이 서로 다른 종목을 보여주게 된다.
        """
        return match(await self.catalog(), query)

    async def close(self) -> None:
        return None


class SymbolCatalog:
    """전체 종목 목록 캐시. 세 가지를 한다.

    1. **TTL** — 상장·폐지는 하루에 몇 번 안 바뀐다. 기본 12시간.
    2. **한 번만 나간다** — 동시 요청이 열 개 와도 거래소에는 한 번만 간다.
       통합 검색이 모든 프로바이더를 한꺼번에 때리므로 이건 실제로 일어나고,
       토스의 429 를 부르는 정확한 구조다.
    3. **낡은 값이라도 준다** — 갱신에 실패하면 마지막 성공분을 `stale` 로 돌려준다.
       429 때문에 화면이 통째로 비는 것보다 하루 지난 목록이 낫다.
    """

    def __init__(self, ttl: float = 12 * 3600) -> None:
        self._ttl = ttl
        self._items: list[dict] = []
        self._at = 0.0
        self._task: asyncio.Task | None = None

    @property
    def fresh(self) -> bool:
        return bool(self._items) and time.time() - self._at < self._ttl

    async def _run(self, build) -> list[dict]:
        found = await build()
        self._items, self._at = found, time.time()
        return found

    async def get(self, build) -> tuple[list[dict], bool]:
        """(목록, 낡았는지). `build` 는 실제로 받아 오는 async 함수.

        **적재를 작업으로 띄우고 `shield` 로 감싼다.** 부르는 쪽이 시간 초과로
        취소돼도 적재는 계속 돌아 캐시를 채운다. 안 그러면 통합 검색이 2.5초에
        잘릴 때마다 적재까지 같이 죽어서, 몇 번을 쳐도 영원히 차갑다 —
        토스는 목록이 4천 건이라 첫 적재가 그 시간을 넘는다.
        """
        if self.fresh:
            return self._items, False
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(build))
        try:
            return await asyncio.shield(self._task), False
        except asyncio.CancelledError:
            raise
        except Exception:                                           # noqa: BLE001
            if self._items:
                return self._items, True                            # 낡아도 없는 것보단 낫다
            raise


class CandleAggregator:
    """체결 틱을 봉으로 접는다.

    Binance 만 완성된 봉을 주고, Upbit·Finnhub·KIS 는 체결만 준다. 접는 규칙을
    프로바이더마다 따로 쓰면 같은 1분봉이 거래소마다 다르게 닫힌다 — 그래서 하나만 둔다.
    """

    def __init__(self, timeframe: str) -> None:
        self.timeframe = timeframe
        self._current: Candle | None = None

    def add(self, ts_ms: int, price: float, volume: float = 0.0) -> list[Candle]:
        """틱 하나를 넣고 내보낼 봉을 돌려준다.

        봉이 넘어가면 [직전 봉(확정), 새 봉(미확정)] 두 개가 나온다. 확정 봉을 먼저
        내보내야 화면이 마지막 봉을 확정 처리한 뒤 새 봉을 연다.
        """
        bucket = floor_ts(int(ts_ms), self.timeframe)
        current = self._current

        if current is None:
            self._current = Candle(bucket, price, price, price, price, volume, closed=False)
            return [self._current]

        if bucket > current.ts:
            closed = Candle(current.ts, current.open, current.high, current.low,
                            current.close, current.volume, closed=True)
            self._current = Candle(bucket, price, price, price, price, volume, closed=False)
            return [closed, self._current]

        if bucket < current.ts:
            return []  # 늦게 도착한 틱. 이미 닫은 봉을 되살리지 않는다.

        self._current = Candle(
            current.ts,
            current.open,
            max(current.high, price),
            min(current.low, price),
            price,
            current.volume + volume,
            closed=False,
        )
        return [self._current]

    def seed(self, candle: Candle) -> None:
        """히스토리의 마지막 미확정 봉을 이어받는다. 안 하면 거래량이 0에서 다시 센다."""
        self._current = candle


_REGISTRY: dict[str, Provider] = {}


def register(provider: Provider) -> Provider:
    _REGISTRY[provider.info.key] = provider
    return provider


def get(key: str) -> Provider:
    try:
        return _REGISTRY[key]
    except KeyError:
        raise ProviderError(f"등록되지 않은 프로바이더: {key!r}") from None


def all_providers() -> list[Provider]:
    return list(_REGISTRY.values())


def describe() -> list[dict]:
    return [
        {
            "key": p.info.key,
            "name": p.info.name,
            "market": p.info.market,
            "timeframes": list(p.info.timeframes),
            "requiresKey": p.info.requires_key,
            "realtime": p.info.realtime,
            "note": p.info.note,
            "available": p.available,
            "reason": p.unavailable_reason,
            "defaultSymbols": list(p.info.default_symbols),
            "listsSymbols": p.info.lists_symbols,
        }
        for p in all_providers()
    ]


__all__ = [
    "Provider", "ProviderInfo", "ProviderError", "ProviderUnavailable", "SymbolNotFound",
    "CandleAggregator", "register", "get", "all_providers", "describe",
    "empty_frame", "field",
]
