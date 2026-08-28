"""프로바이더 계약.

메서드는 둘뿐이다 — `history` 와 `stream`. 시장별 분기(`if market == "kr"`)를 엔진이나
API 나 화면에 흘리지 않기 위해서다. 거래소가 ms 를 주든 KST 문자열을 주든,
그 차이는 이 계층 안에서 끝난다.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import AsyncIterator

import pandas as pd

from ..core.candle import Candle, empty_frame
from ..core.timeframe import floor_ts


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

    async def search(self, query: str) -> list[dict]:
        return []

    async def close(self) -> None:
        return None


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
        }
        for p in all_providers()
    ]


__all__ = [
    "Provider", "ProviderInfo", "ProviderError", "ProviderUnavailable", "SymbolNotFound",
    "CandleAggregator", "register", "get", "all_providers", "describe",
    "empty_frame", "field",
]
