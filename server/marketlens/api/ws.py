"""실시간 팬아웃.

거래소로 나가는 연결은 (프로바이더, 심볼, 타임프레임) 당 **하나**다. 브라우저 탭이
열 개 열려도 Binance 쪽 소켓은 하나다 — 탭마다 새로 연결하면 거래소 한도에 먼저 걸린다.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import pandas as pd

from ..core.candle import Candle, upsert
from ..providers import ProviderError, get as get_provider

log = logging.getLogger("marketlens.ws")

# 스트림이 끊겼을 때 다시 붙기까지. 그대로 죽이면 밤새 켜 둔 화면이 조용히 멈춘다.
RECONNECT_DELAY = 3.0
MAX_BARS = 1500


@dataclass
class Room:
    """하나의 (프로바이더, 심볼, 타임프레임)."""

    provider: str
    symbol: str
    timeframe: str
    df: pd.DataFrame
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    task: asyncio.Task | None = None

    @property
    def key(self) -> tuple:
        return (self.provider, self.symbol, self.timeframe)


class StreamHub:
    def __init__(self) -> None:
        self._rooms: dict[tuple, Room] = {}
        self._lock = asyncio.Lock()

    async def join(
        self, provider: str, symbol: str, timeframe: str, history: pd.DataFrame
    ) -> tuple[Room, asyncio.Queue]:
        key = (provider, symbol, timeframe)
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        async with self._lock:
            room = self._rooms.get(key)
            if room is None:
                room = Room(provider, symbol, timeframe, history.copy())
                self._rooms[key] = room
                room.task = asyncio.create_task(self._pump(room))
            room.subscribers.add(queue)
        return room, queue

    async def leave(self, room: Room, queue: asyncio.Queue) -> None:
        async with self._lock:
            room.subscribers.discard(queue)
            if not room.subscribers:
                # 마지막 사람이 나가면 거래소 연결도 닫는다.
                self._rooms.pop(room.key, None)
                if room.task:
                    room.task.cancel()

    async def _pump(self, room: Room) -> None:
        provider = get_provider(room.provider)
        while True:
            try:
                async for candle in provider.stream(room.symbol, room.timeframe):
                    room.df = upsert(room.df, candle).tail(MAX_BARS).reset_index(drop=True)
                    self._broadcast(room, candle)
            except asyncio.CancelledError:
                raise
            except (ProviderError, OSError) as exc:
                log.warning("%s 스트림 끊김: %s", room.key, exc)
                self._broadcast_error(room, str(exc))
            except Exception as exc:  # noqa: BLE001
                log.exception("%s 스트림 실패", room.key)
                self._broadcast_error(room, str(exc))
            await asyncio.sleep(RECONNECT_DELAY)

    @staticmethod
    def _put(queue: asyncio.Queue, message: dict) -> None:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            # 느린 구독자 때문에 다른 사람의 실시간이 밀리면 안 된다. 그 사람 것만 버린다.
            pass

    def _broadcast(self, room: Room, candle: Candle) -> None:
        message = {
            "type": "candle",
            "candle": {
                "time": candle.ts // 1000,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
                "closed": candle.closed,
            },
        }
        for queue in room.subscribers:
            self._put(queue, message)

    def _broadcast_error(self, room: Room, reason: str) -> None:
        for queue in room.subscribers:
            self._put(queue, {"type": "streamError", "reason": reason})


hub = StreamHub()
