"""장부가 필요한 시세 — 지금 값과 변동성.

포지션 화면은 **평가손익**을 보여줘야 하고 그건 지금 값이 있어야 난다. 종목마다
부르면 열 종목에 열 번이라, 화면 한 번에 한 종목당 한 번으로 묶는다.

ATR 은 손으로 연 포지션의 손절·목표를 뽑을 때만 쓴다. 계산은 `indicators/_math.py`
한 곳이고 여기서 다시 만들지 않는다 — 평활 방식이 갈리면 같은 종목의 ATR 이 화면과
장부에서 다르게 나온다.
"""
from __future__ import annotations

import asyncio
import logging

from ..core.candle import closed_only
from ..indicators._math import atr
from ..providers import get as get_provider

log = logging.getLogger("marketlens.positions")

TIMEOUT = 12.0
ATR_PERIOD = 14


async def _last(provider_key: str, symbol: str) -> float | None:
    provider = get_provider(provider_key)
    frame = await asyncio.wait_for(provider.history(symbol, "1m", 2), TIMEOUT)
    if frame is None or frame.empty:
        return None
    return float(frame["close"].iloc[-1])


async def last_prices(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], float]:
    """(시장, 종목) → 지금 값. **못 받은 것은 빠진다** — 화면은 그걸 '—' 로 그린다.

    하나가 실패해도 나머지 평가손익은 나와야 한다. 국내주식은 IP 를 안 걸어 두면
    통째로 실패하는데, 그때 코인 포지션까지 값이 비면 장부를 못 읽는다.
    """
    unique = list(dict.fromkeys(pairs))
    if not unique:
        return {}
    got = await asyncio.gather(*[_last(p, s) for p, s in unique],
                              return_exceptions=True)
    out: dict[tuple[str, str], float] = {}
    for key, value in zip(unique, got):
        if isinstance(value, BaseException):
            log.warning("시세 실패 %s:%s — %s", key[0], key[1], str(value)[:60])
            continue
        if value is not None:
            out[key] = value
    return out


async def atr_pct(provider_key: str, symbol: str) -> float | None:
    """가격 대비 ATR(14). 밴드가 없는 포지션의 손절·목표를 여기서 뽑는다."""
    try:
        provider = get_provider(provider_key)
        frame = await asyncio.wait_for(
            provider.history(symbol, "1d", ATR_PERIOD * 4), TIMEOUT)
    except Exception as exc:                                   # noqa: BLE001
        log.warning("ATR 실패 %s:%s — %s", provider_key, symbol, str(exc)[:60])
        return None
    if frame is None or frame.empty:
        return None
    # **확정된 봉만.** 안 닫힌 마지막 봉의 고저는 아직 커지는 중이라 ATR 이 흔들린다.
    closed = closed_only(frame)
    if len(closed) < ATR_PERIOD + 1:
        return None
    value = float(atr(closed, ATR_PERIOD).iloc[-1])
    last = float(closed["close"].iloc[-1])
    if not last or value != value:                             # NaN 방어
        return None
    return value / last
