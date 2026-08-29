"""오늘의 추천을 만들어 내보내는 곳.

여기서 재지 않는다. `scripts/screen.py` 가 재 놓은 `factors.json` 을 읽어, **마지막
확정봉**의 팩터로 후보 종목을 줄 세우기만 한다. 재 놓은 게 없으면 순위를 만들지 않고
그렇게 답한다.

확정봉만 쓰는 이유는 리페인팅이다. 진행 중인 봉으로 순위를 매기면 같은 날 오전과
오후의 추천이 달라지고, 그건 추천이 아니라 시세 중계다.

## 두 폴더를 **합쳐** 읽는다

`learning/` 은 GitHub Actions 가, `learning-local/` 은 이 PC 가 쓴다. 토스(국내주식)는
IP 제한 때문에 PC 에서만 잴 수 있으므로, 하나만 읽으면 반쪽이 사라진다 — 로컬 파일에
토스만 있으면 저장소가 잰 암호화폐·미국주식을 통째로 잃는다.

`api/learning.py` 의 챔피언은 **합치지 않고 하나만** 읽는다. 일부러 다르게 뒀다:
팩터는 프로바이더 단위로 쪼개져 있어 합쳐도 뜻이 안 변하지만, 챔피언은 `store_data`
안의 모델 파일과 짝이라 반쪽만 가져오면 설정과 모델이 어긋난다. 둘을 통일하려다
챔피언 쪽을 망가뜨리지 말 것.
"""
from __future__ import annotations

import asyncio
import json

import pandas as pd

from .. import events
from ..core.candle import closed_only
from ..forecast.ml import market
from ..screen import factors, rank, universe
from .learning import DIRS

FILE = "factors.json"
# 팩터를 데우는 데 필요한 봉 수. 평소대비 창(120) + 지표 워밍업.
BARS = 600


def _measured() -> dict:
    """두 폴더를 프로바이더 단위로 합친다. 겹치면 `learning-local` 이 이긴다."""
    merged: dict = {"providers": {}}
    # `DIRS` 는 로컬이 먼저다. 뒤에 읽은 것이 이기게 하려면 거꾸로 돌아야 한다.
    for folder in reversed(DIRS):
        path = folder / FILE
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key in ("updated", "timeframe", "minIc"):
            if raw.get(key) is not None:
                merged[key] = raw[key]
        merged["providers"].update(raw.get("providers") or {})
    return merged if merged["providers"] else {}


def _entry(raw: dict, provider: str) -> dict:
    return (raw.get("providers") or {}).get(provider) or {}


def _timeframe_of(raw: dict, entry: dict) -> str | None:
    """이 프로바이더를 무슨 봉으로 쟀나. 항목 안이 먼저, 없으면 옛 파일의 최상위."""
    return entry.get("timeframe") or raw.get("timeframe")


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
            name: {
                "horizons": sorted(int(h) for h in (body.get("horizons") or {})),
                "timeframe": _timeframe_of(raw, body),
                "updated": body.get("updated") or raw.get("updated"),
            }
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
    raw = _measured()                    # 한 번만 읽는다 — 두 번 읽으면 그 사이에 바뀐다
    measured = _entry(raw, provider)
    if not measured:
        return {"available": False,
                "measuredProviders": sorted(raw.get("providers") or {}),
                "reason": f"{provider} 는 아직 안 쟀다 — "
                          f"`python scripts/screen.py --provider {provider}` 로 먼저 잰다"}

    # **잰 봉과 물은 봉이 다르면 순위를 만들지 않는다.** 일봉으로 잰 IC 부호를 시간봉
    # 팩터에 그대로 곱하면 조용히 거짓말하는 순위가 나온다. 빈 화면이 낫다.
    measured_tf = _timeframe_of(raw, measured)
    if measured_tf and measured_tf != timeframe:
        return {"available": False, "measuredTimeframe": measured_tf,
                "reason": f"{provider} 는 {measured_tf} 봉으로 쟀는데 {timeframe} 봉을 "
                          f"물었다 — 다른 봉의 IC 부호를 그대로 쓰면 순위가 거짓말이 된다"}

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
    # **전날 등락률을 안 낸다.** 앞을 보는 화면에 지나간 값을 큰 숫자로 띄우면
    # 그게 예측인 줄로 읽힌다 — 실제로 그렇게 읽혔다. 마지막 종가만 넘긴다.
    prices: dict[str, float] = {}
    for symbol, closed, found, attn in loaded:
        panel = factors.panel(closed, found, horizon=horizon, attention_frame=attn,
                              market_frame=market.features(closed, series))
        if panel.empty:
            continue
        row = factors.with_relative(panel).iloc[-1]
        latest[symbol] = row.drop(labels=["ts"], errors="ignore")
        prices[symbol] = round(float(closed["close"].astype("float64").iloc[-1]), 6)

    result = rank.build(latest, measured, horizon, limit, prices)
    result["provider"] = provider
    result["timeframe"] = timeframe
    result["measuredAt"] = measured.get("updated") or raw.get("updated")
    if skipped:
        result["skipped"] = skipped
    return result


def horizons(provider: str) -> list[int]:
    raw = _measured()
    return sorted(int(h) for h in (_entry(raw, provider).get("horizons") or {}))
