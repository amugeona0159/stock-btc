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
from ..providers import ProviderError, ProviderUnavailable, get as get_provider

log = logging.getLogger("marketlens.ws")

# 스트림이 끊겼을 때 다시 붙기까지. 그대로 죽이면 밤새 켜 둔 화면이 조용히 멈춘다.
RECONNECT_DELAY = 3.0
MAX_BARS = 1500
# 이 횟수를 연달아 실패하면 재시도를 멈춘다. 무한 재시도는 거래소 차단만 길게 만든다.
MAX_FAILURES = 6


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
        if not provider.info.realtime:
            # 실시간이 없는 프로바이더(야후 등)는 한 번 알리고 끝낸다.
            # 3초마다 같은 오류를 다시 던지면 화면이 계속 빨간불로 깜빡인다.
            self._broadcast_static(room, f"{provider.info.name} 은 실시간을 주지 않는다 "
                                         "— 과거 캔들까지만 볼 수 있다")
            return

        failures = 0
        while True:
            try:
                async for candle in provider.stream(room.symbol, room.timeframe):
                    failures = 0
                    room.df = upsert(room.df, candle).tail(MAX_BARS).reset_index(drop=True)
                    self._broadcast(room, candle)
            except asyncio.CancelledError:
                raise
            except ProviderUnavailable as exc:
                # 키가 없는 것은 기다린다고 해결되지 않는다. 재시도하지 않는다.
                self._broadcast_static(room, str(exc))
                return
            except (ProviderError, OSError) as exc:
                failures += 1
                log.warning("%s 스트림 끊김(%d회): %s", room.key, failures, exc)
                self._broadcast_error(room, str(exc))
            except Exception as exc:  # noqa: BLE001
                failures += 1
                log.exception("%s 스트림 실패", room.key)
                self._broadcast_error(room, str(exc))

            if failures >= MAX_FAILURES:
                self._broadcast_static(
                    room,
                    f"실시간 연결이 {failures}번 실패해 멈췄다. 새로고침하면 다시 시도한다.",
                )
                return
            # 연달아 실패하면 간격을 늘린다. 거래소가 막았을 때 초당 재접속하면
            # 차단만 길어진다.
            await asyncio.sleep(min(RECONNECT_DELAY * (2 ** (failures - 1)), 60.0))

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

    def _broadcast_static(self, room: Room, reason: str) -> None:
        """더 시도하지 않는다는 통지. 화면은 이걸 오류가 아니라 상태로 표시한다."""
        for queue in room.subscribers:
            self._put(queue, {"type": "streamStopped", "reason": reason})


hub = StreamHub()
