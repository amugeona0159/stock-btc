"""오늘의 추천을 만들어 내보내는 곳.

여기서 재지 않는다. `scripts/screen.py` 가 재 놓은 `learning/factors.json` 을 읽어,
**마지막 확정봉**의 팩터로 후보 종목을 줄 세우기만 한다. 재 놓은 게 없으면 순위를
만들지 않고 그렇게 답한다.

확정봉만 쓰는 이유는 리페인팅이다. 진행 중인 봉으로 순위를 매기면 같은 날 오전과
오후의 추천이 다르고, 그건 추천이 아니라 시세 중계다.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pandas as pd

from .. import events
from ..core.candle import closed_only
from ..forecast.ml import market
from ..screen import factors, rank, universe

# 자동 학습 기록과 같은 규칙 — 이 PC 가 잰 게 있으면 그쪽이 맞다.
DIRS = (Path("learning-local"), Path("learning"))
FILE = "factors.json"
# 팩터를 데우는 데 필요한 봉 수. 평소대비 창(120) + 지표 워밍업.
BARS = 600


def _measured() -> dict:
    for folder in DIRS:
        path = folder / FILE
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
    return {}


def status() -> dict:
    """무엇을 언제 쟀는지. 화면에서 '아직 안 쟀다'를 설명하는 데 쓴다."""
    raw = _measured()
    providers = raw.get("providers", {})
    return {
        "available": bool(providers),
        "updated": raw.get("updated"),
        "timeframe": raw.get("timeframe"),
        "minIc": raw.get("minIc"),
        "providers": {
            name: sorted(int(h) for h in (body.get("horizons") or {}))
            for name, body in providers.items()
        },
    }


async def _one(load_candles, provider: str, symbol: str, timeframe: str, market_name: str):
    """한 종목의 시세·사건·관심도. 하나가 실패해도 나머지로 순위를 만든다."""
    from ..events.sources import attention as attention_source

    df = await load_candles(provider, symbol, timeframe, BARS)
    closed = closed_only(df).reset_index(drop=True)
    if len(closed) < 200:
        raise ValueError(f"{symbol}: 봉이 {len(closed)}개뿐")
    found, _ = await events.collect(df, symbol, market_name)
    relevant = events.relevant(found, symbol, market_name)
    frame, _ = await attention_source.collect(closed, symbol)
    return symbol, closed, relevant, frame


async def build(load_candles, provider: str, timeframe: str, horizon: int,
                limit: int, market_name: str) -> dict:
    """오늘의 순위. `load_candles` 는 라우트가 쓰는 캐시된 적재 함수를 그대로 받는다."""
    measured = ((_measured().get("providers") or {}).get(provider) or {})
    if not measured:
        return {"available": False,
                "reason": f"{provider} 는 아직 안 쟀다 — "
                          f"`python scripts/screen.py --provider {provider}` 로 먼저 잰다"}

    symbols = universe.symbols(provider)
    gathered = await asyncio.gather(
        *(_one(load_candles, provider, s, timeframe, market_name) for s in symbols),
        return_exceptions=True,
    )
    loaded = [g for g in gathered if not isinstance(g, BaseException)]
    skipped = [{"symbol": s, "reason": str(g)[:120]}
               for s, g in zip(symbols, gathered) if isinstance(g, BaseException)]
    if len(loaded) < universe.MIN_BREADTH:
        return {"available": False, "skipped": skipped,
                "reason": f"시세를 받은 종목이 {len(loaded)}개뿐 — 횡단면 순위가 안 된다"}

    series = market.market_series({s: c for s, c, _, _ in loaded})
    latest: dict[str, pd.Series] = {}
    prices: dict[str, tuple[float, float]] = {}
    for symbol, closed, found, attn in loaded:
        panel = factors.panel(closed, found, horizon=horizon, attention_frame=attn,
                              market_frame=market.features(closed, series))
        if panel.empty:
            continue
        row = factors.with_relative(panel).iloc[-1]
        latest[symbol] = row.drop(labels=["ts"], errors="ignore")
        close = closed["close"].astype("float64")
        prices[symbol] = (round(float(close.iloc[-1]), 6),
                          round(float(close.iloc[-1] / close.iloc[-2] - 1) * 100, 3)
                          if len(close) > 1 else 0.0)

    result = rank.build(latest, measured, horizon, limit, prices)
    result["provider"] = provider
    result["timeframe"] = timeframe
    result["measuredAt"] = _measured().get("updated")
    if skipped:
        result["skipped"] = skipped
    return result


def horizons(provider: str) -> list[int]:
    measured = ((_measured().get("providers") or {}).get(provider) or {}).get("horizons") or {}
    return sorted(int(h) for h in measured)
