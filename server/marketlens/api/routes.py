"""HTTP 엔드포인트.

지표 계산은 전부 여기를 지난다. 프론트는 받은 시리즈를 그리기만 한다 — 양쪽에서 계산하면
반올림과 시드값 차이로 같은 화면 안의 20EMA 두 개가 어긋난다.
"""
from __future__ import annotations

import asyncio

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import analog, events, research, scenario
from .. import forecast as forecast_layer
from ..backtest import engine as backtest_engine
from ..context import features as context_features
from ..core.candle import closed_only
from ..core.text import with_josa
from ..core.series import IndicatorRequest, candles_payload, compute_requests
from ..indicators import catalog, patterns
from ..providers import (ProviderError, ProviderUnavailable, SymbolNotFound,
                         describe, get as get_provider)
from ..signals.engine import evaluate
from . import learning
from ..store.cache import cache

router = APIRouter(prefix="/api")

MAX_LIMIT = 5000


async def load_candles(provider_key: str, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    provider = _provider(provider_key)
    if not provider.supports(timeframe):
        raise HTTPException(
            400, f"{with_josa(provider.info.name, '은는')} {timeframe} 봉을 주지 않는다"
        )

    cached = cache.get(provider_key, symbol, timeframe, limit)
    if cached is not None:
        return cached
    try:
        df = await provider.history(symbol, timeframe, limit)
    except SymbolNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProviderUnavailable as exc:
        raise HTTPException(400, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
    if df.empty:
        raise HTTPException(404, f"{symbol} 캔들이 비어 있다")
    cache.put(provider_key, symbol, timeframe, df)
    return df


def _provider(key: str):
    try:
        return get_provider(key)
    except ProviderError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/providers")
def providers() -> dict:
    return {"providers": describe()}


@router.get("/indicators")
def indicators() -> dict:
    """지표 카탈로그. 화면의 지표 패널이 통째로 이걸 보고 만들어진다."""
    return {
        "categories": catalog.categories(),
        "indicators": catalog.catalog(),
        "defaults": [dict(item) for item in catalog.DEFAULT_SET],
    }


@router.get("/search")
async def search(provider: str, q: str = Query(min_length=1)) -> dict:
    try:
        return {"results": await _provider(provider).search(q)}
    except ProviderUnavailable as exc:
        raise HTTPException(400, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/candles")
async def candles(
    provider: str, symbol: str, timeframe: str = "1h",
    limit: int = Query(500, ge=10, le=MAX_LIMIT),
) -> dict:
    df = await load_candles(provider, symbol, timeframe, limit)
    return {
        "provider": provider,
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": candles_payload(df),
    }


class AnalyzeBody(BaseModel):
    provider: str
    symbol: str
    timeframe: str = "1h"
    limit: int = Field(500, ge=50, le=MAX_LIMIT)
    indicators: list[dict] = Field(default_factory=list)
    horizon: int = Field(10, ge=1, le=200)
    model: str | None = None


@router.post("/analyze")
async def analyze(body: AnalyzeBody) -> dict:
    df = await load_candles(body.provider, body.symbol, body.timeframe, body.limit)
    requests = [
        IndicatorRequest.parse(raw, i)
        for i, raw in enumerate(body.indicators or [dict(d) for d in catalog.DEFAULT_SET])
    ]
    # 시그널과 예측은 무거워서 CPU 를 오래 문다. 이벤트 루프를 막지 않게 스레드로 뺀다.
    signal, prediction, situation = await asyncio.gather(
        asyncio.to_thread(evaluate, df),
        asyncio.to_thread(forecast_layer.combined, df, body.timeframe, body.horizon, body.model),
        asyncio.to_thread(context_features.describe, closed_only(df)),
    )
    return {
        "provider": body.provider,
        "symbol": body.symbol,
        "timeframe": body.timeframe,
        "candles": candles_payload(df),
        "indicators": compute_requests(df, requests, body.timeframe),
        "signal": signal.to_dict(),
        "forecast": prediction,
        "patterns": patterns.latest(df, lookback=5),
        # 질문하기 전에도 화면이 지금 상황을 말해 줘야 한다. 빈 입력창만 있는 첫 화면은
        # 무엇을 물어야 할지 모르게 만든다.
        "situation": situation,
    }


class BacktestBody(BaseModel):
    provider: str
    symbol: str
    timeframe: str = "1h"
    limit: int = Field(1000, ge=200, le=MAX_LIMIT)
    warmup: int = Field(120, ge=30, le=1000)
    threshold: float = Field(0.15, ge=0.0, le=1.0)
    fee: float = Field(0.0005, ge=0.0, le=0.05)
    slippage: float = Field(0.0005, ge=0.0, le=0.05)
    allow_short: bool = True


@router.post("/backtest")
async def backtest(body: BacktestBody) -> dict:
    df = await load_candles(body.provider, body.symbol, body.timeframe, body.limit)
    result = await asyncio.to_thread(
        backtest_engine.run, df,
        backtest_engine.signal_strategy(body.threshold),
        body.warmup, body.fee, body.slippage, body.allow_short,
    )
    payload = result.to_dict()
    payload["symbol"] = body.symbol
    payload["timeframe"] = body.timeframe
    return payload


class ProjectBody(BaseModel):
    provider: str
    symbol: str
    timeframe: str = "1h"
    # 사례를 찾으려면 히스토리가 깊어야 한다. 500봉으로는 비슷한 구간이 몇 개 안 나온다.
    limit: int = Field(3000, ge=300, le=MAX_LIMIT)
    window: int = Field(48, ge=8, le=400)
    horizon: int = Field(24, ge=1, le=400)
    top_k: int = Field(20, ge=3, le=100)
    context_weight: float = Field(0.5, ge=0.0, le=1.0)
    group_weights: dict[str, float] | None = None
    peers: list[str] = Field(default_factory=list)


@router.post("/project")
async def project(body: ProjectBody) -> dict:
    """지금과 비슷했던 과거를 찾아 앞으로의 경로를 그린다."""
    df = await load_candles(body.provider, body.symbol, body.timeframe, body.limit)
    closed = closed_only(df)
    if len(closed) < body.window + body.horizon + 30:
        raise HTTPException(400, "사례를 찾을 만큼 과거가 없다 — 기간을 늘리거나 창을 줄일 것")

    sources = [analog.Series(f"{body.symbol}-{body.timeframe}", closed)]
    # 다른 종목까지 뒤지면 사례가 늘지만, 그만큼 '남의 상황'이 섞인다. 기본은 자기 과거만.
    for peer in body.peers[:5]:
        if peer.upper() == body.symbol.upper():
            continue
        try:
            peer_df = closed_only(
                await load_candles(body.provider, peer, body.timeframe, body.limit)
            )
        except HTTPException:
            continue
        if len(peer_df) >= body.window + body.horizon + 30:
            sources.append(analog.Series(f"{peer}-{body.timeframe}", peer_df))

    matches = await asyncio.to_thread(
        analog.search, closed, sources, body.window, body.horizon,
        body.top_k, body.context_weight, body.group_weights,
    )
    projection = await asyncio.to_thread(
        analog.project, closed, matches, body.horizon, body.timeframe
    )
    situation = await asyncio.to_thread(context_features.describe, closed)

    return {
        "provider": body.provider,
        "symbol": body.symbol,
        "timeframe": body.timeframe,
        "window": body.window,
        "situation": situation,
        "projection": projection,
        "matches": [m.to_dict() for m in matches],
    }


class AskBody(BaseModel):
    provider: str
    symbol: str
    timeframe: str = "1h"
    question: str = ""
    limit: int = Field(3000, ge=300, le=MAX_LIMIT)
    window: int = Field(48, ge=8, le=400)
    top_k: int = Field(20, ge=3, le=100)
    # 폼에서 고친 값. 있으면 자연어 해석보다 이쪽이 이긴다 — 사람이 마지막 결정권을 갖는다.
    form: dict | None = None
    use_llm: bool = True
    event_sources: list[str] | None = None


@router.post("/ask")
async def ask(body: AskBody) -> dict:
    """질문 하나로 조건을 잡고, 그 조건에 맞는 과거를 찾아 앞을 그린다."""
    if not body.question.strip() and not body.form:
        raise HTTPException(400, "질문이나 조건 중 하나는 있어야 한다")

    provider_info = _provider(body.provider).info
    df = await load_candles(body.provider, body.symbol, body.timeframe, body.limit)

    if body.form is not None:
        try:
            plan = scenario.from_form(body.form, body.question, body.timeframe)
        except Exception as exc:  # pydantic 검증 실패를 사용자 오류로 돌려준다
            raise HTTPException(400, f"조건이 올바르지 않다: {exc}") from exc
    else:
        plan = await scenario.parse(body.question, body.timeframe, body.use_llm)

    sources = tuple(body.event_sources) if body.event_sources else events.DEFAULT_SOURCES
    found, status = await events.collect(df, body.symbol, provider_info.market, sources)

    result = await asyncio.to_thread(
        scenario.run, df, body.symbol, provider_info.market, plan, found,
        body.window, body.top_k,
    )
    result["eventSources"] = status
    result["symbol"] = body.symbol
    result["provider"] = body.provider
    # 학습 모델이 있으면 그 곡선도 같이. 검색 기반과 나란히 놓고 봐야
    # 둘이 다른 말을 할 때 그걸 알 수 있다.
    result["learned"] = await _learned_layer(
        df, body.provider, body.symbol, body.timeframe,
        events.relevant(found, body.symbol, provider_info.market),
    )
    return result


async def _learned_layer(df, provider: str, symbol: str, timeframe: str,
                         relevant) -> dict:
    from ..forecast.ml import model as ml_model

    name = model_name(provider, symbol, timeframe)
    if ml_model.load(name) is None:
        return {"available": False,
                "reason": f"{symbol} {timeframe} 로 학습된 모델이 없다 — '학습' 에서 만들 수 있다"}
    try:
        from ..events.sources import attention as attention_source
        frame, _ = await attention_source.collect(closed_only(df).reset_index(drop=True), symbol)
        return await asyncio.to_thread(ml_model.predict, df, name, relevant, timeframe, frame)
    except Exception as exc:  # noqa: BLE001 - 학습층이 없다고 답 전체가 막히면 안 된다
        return {"available": False, "reason": str(exc)}


class EventsBody(BaseModel):
    provider: str
    symbol: str
    timeframe: str = "1h"
    limit: int = Field(2000, ge=300, le=MAX_LIMIT)
    sources: list[str] | None = None
    gdelt_query: str | None = None


@router.post("/events")
async def list_events(body: EventsBody) -> dict:
    """구간에 걸린 사건 전부. 차트 위에 표시하고, 클릭하면 그때로 간다."""
    provider_info = _provider(body.provider).info
    df = await load_candles(body.provider, body.symbol, body.timeframe, body.limit)
    sources = tuple(body.sources) if body.sources else events.DEFAULT_SOURCES
    found, status = await events.collect(df, body.symbol, provider_info.market,
                                         sources, body.gdelt_query)
    relevant = events.relevant(found, body.symbol, provider_info.market)
    return {
        "sources": status,
        "available": list(events.ALL_SOURCES),
        "count": len(relevant),
        # 사건이 수백 건이면 화면이 못 읽는다. 굵직한 것부터 자른다.
        "events": [e.to_dict() for e in sorted(relevant, key=lambda e: -e.severity)[:300]],
    }


class UserEventBody(BaseModel):
    at: str                      # ISO8601
    title: str
    kind: str = "user"
    scope: str = "global"
    severity: float = Field(0.5, ge=0.0, le=1.0)
    scheduled: bool = False
    url: str = ""
    tags: list[str] = Field(default_factory=list)
    note: str = ""


@router.post("/events/user")
def add_user_event(body: UserEventBody) -> dict:
    """사용자가 아는 사건을 직접 등록한다. 어떤 API 보다 정확한 자료다."""
    try:
        event = events.store.add(body.model_dump())
    except Exception as exc:
        raise HTTPException(400, f"사건을 저장하지 못했다: {exc}") from exc
    return {"event": event.to_dict()}


@router.delete("/events/user/{event_id}")
def delete_user_event(event_id: str) -> dict:
    if not events.store.remove(event_id):
        raise HTTPException(404, "그런 사건이 없다")
    return {"removed": event_id}


@router.get("/events/user")
def list_user_events() -> dict:
    return {"events": [e.to_dict() for e in events.store.all_events()]}


@router.get("/research")
def research_library() -> dict:
    """근거 등록부 전체. 화면의 '근거' 패널이 이걸 그대로 그린다."""
    return {"entries": [e.to_dict() for e in research.all_entries()]}


# 함께 학습할 기본 동료 종목. 한 종목 몇천 행으로는 표본이 모자란다 —
# 축이 전부 무차원이라 다른 종목을 섞어도 같은 표에 들어간다.
PEERS = {
    # 많을수록 좋다. 축이 전부 무차원이라 종목이 늘수록 표본만 커진다.
    "binance": ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT",
                "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "LTCUSDT"),
    "upbit": ("KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE"),
    "yahoo": ("AAPL", "MSFT", "NVDA", "AMZN", "^GSPC"),
    "us_stock": ("AAPL", "MSFT", "NVDA", "AMZN", "TSLA"),
    "toss_us": ("AAPL", "MSFT", "NVDA", "AMZN", "TSLA"),
    "toss_kr": ("005930", "000660", "035720", "005380", "051910"),
    "kis": ("005930", "000660", "035720", "005380"),
}
# `scripts/sweep.py` 로 잰 결과. 봉과 지평에 따라 학습이 되는 자리가 정해져 있다.
# (지평 하한, 지평 상한). 이 밖에서는 대개 변동성 기준선이 그대로 쓰인다.
LEARNABLE = {
    "15m": (4, 24),     # 8종목 × 1.2만봉(9만 행) 에서 +0.002
    "1h": (3, 12),      # 12종목 × 1.2만봉(14만 행) 에서 +0.002~+0.008
    "1d": (5, 20),      # 6종목 × 3천봉 에서 +0.005
    "1w": (2, 8),       # 미측정 — 일봉에서 유추
}
# 학습이 되려면 표본이 커야 한다. 시간봉은 한 종목 몇천 행으로는 절대 안 넘는다 —
# 실제로 4천봉 6종목에서는 −0.11 이었고, 1.2만봉 12종목에서 +0.010 이 됐다.
TRAIN_BARS = {"1m": 20000, "5m": 20000, "15m": 15000, "30m": 12000,
              "1h": 12000, "4h": 6000, "1d": 3000, "1w": 1200}
# 학습은 차트보다 훨씬 긴 구간을 본다. `MAX_LIMIT`(차트 한 번에 그릴 봉 수)로 막으면
# 시간봉이 5천봉에 잘려 스윕에서 이겼던 자리를 화면에서 재현할 수 없다 — 실제로
# 그렇게 잘려 있었고, +0.010 이 나와야 할 자리가 +0.000 으로 보였다.
TRAIN_MAX = 20000
# as-of 검증(scripts/asof.py)에서 잰 '실제로 맞은 정도'. 종목 성격에 따라 크게 다르다.
ASOF_NOTE = {
    "us": "주식·지수에서는 잘 맞는다 — S&P500 일봉 10봉 지평에서 방향 80%, 경로상관 +0.38.",
    "kr": "국내주식은 아직 as-of 로 재 보지 않았다.",
    "crypto": "암호화폐 일봉은 방향이 거의 동전던지기였다(BTC 54%, ETH 51%). "
              "밴드 폭은 쓸 만하지만 방향은 믿지 말 것.",
}


class TrainBody(BaseModel):
    provider: str
    symbol: str
    timeframe: str = "1d"
    # 비우면 봉 단위에 맞춰 정한다. 시간봉은 몇천 행으로는 절대 안 넘는다.
    limit: int | None = Field(None, ge=600, le=TRAIN_MAX)
    horizon: int = Field(10, ge=2, le=200)
    window: int = Field(48, ge=8, le=400)
    folds: int = Field(4, ge=2, le=8)
    # 함께 학습할 종목. 비우면 프로바이더별 기본 목록을 쓴다. []는 단일 종목 학습.
    peers: list[str] | None = None
    use_attention: bool = True
    event_sources: list[str] | None = None


def model_name(provider: str, symbol: str, timeframe: str) -> str:
    return f"{provider}-{symbol}-{timeframe}".lower()


def learnable_note(timeframe: str, horizon: int, market: str = "",
                   provider: str = "", symbol: str = "") -> str | None:
    """이 봉·지평에서 학습이 통할 가능성. 재 본 결과를 미리 알려 준다.

    자동 학습이 **이 종목을** 직접 재 놨으면 그 값이 봉 단위 일반론보다 낫다.
    """
    parts: list[str] = []
    measured = learning.note(provider, symbol, timeframe) if provider and symbol else None
    if measured:
        parts.append(measured)
    band = LEARNABLE.get(timeframe)
    if band is None:
        parts.append(f"{timeframe} 봉에서는 학습이 기준선을 넘은 적이 없다 — "
                     "재 봐도 대개 기준선이 그대로 쓰인다.")
    elif not band[0] <= horizon <= band[1]:
        parts.append(f"{timeframe} 봉은 {band[0]}~{band[1]}봉 지평에서만 기준선을 넘었다. "
                     f"{horizon}봉은 그 밖이다.")
    note = ASOF_NOTE.get(market)
    if note:
        parts.append(note)
    return " ".join(parts) or None


async def _symbol_data(provider: str, symbol: str, timeframe: str, limit: int,
                       market: str, sources: tuple, use_attention: bool):
    """학습에 넣을 한 종목 — 시세 + 사건 + 관심도."""
    from ..events.sources import attention as attention_source
    from ..forecast.ml.model import SymbolData

    df = await load_candles(provider, symbol, timeframe, limit)
    found, status = await events.collect(df, symbol, market, sources)
    relevant = events.relevant(found, symbol, market)
    frame = None
    if use_attention:
        frame, _ = await attention_source.collect(closed_only(df).reset_index(drop=True), symbol)
    return SymbolData(symbol, df, relevant, frame), status


@router.post("/train")
async def train(body: TrainBody) -> dict:
    """이 종목·봉에서 학습이 실제로 무언가를 더하는지 재고, 되면 저장한다."""
    from ..forecast.ml import model as ml_model

    provider_info = _provider(body.provider).info
    sources = tuple(body.event_sources) if body.event_sources else events.DEFAULT_SOURCES
    peers = body.peers if body.peers is not None else list(PEERS.get(body.provider, ()))
    limit = body.limit or min(TRAIN_MAX, TRAIN_BARS.get(body.timeframe, 3000))

    wanted = [body.symbol] + [p for p in peers if p.upper() != body.symbol.upper()]
    datasets, status, skipped = [], {}, []
    for symbol in wanted[:12]:
        try:
            data, source_status = await _symbol_data(
                body.provider, symbol, body.timeframe, limit,
                provider_info.market, sources, body.use_attention,
            )
        except HTTPException as exc:
            # 동료 종목 하나가 없다고 학습 전체를 막지 않는다. 다만 조용히 넘어가지도 않는다.
            skipped.append({"symbol": symbol, "reason": str(exc.detail)})
            if symbol.upper() == body.symbol.upper():
                raise
            continue
        datasets.append(data)
        status = status or source_status

    name = model_name(body.provider, body.symbol, body.timeframe)
    try:
        report = await asyncio.to_thread(
            ml_model.train, datasets, name, body.horizon, body.window,
            body.folds, body.timeframe,
        )
    except ml_model.MissingDependency as exc:
        raise HTTPException(501, str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "model": name, "report": report, "eventSources": status,
        "skipped": skipped, "bars": limit,
        "note": learnable_note(body.timeframe, body.horizon, provider_info.market,
                               body.provider, body.symbol),
    }


@router.get("/models")
def models() -> dict:
    from ..forecast.ml import model as ml_model

    return {"models": ml_model.available()}


@router.get("/learning")
def learning_state() -> dict:
    """매일 도는 자동 학습이 지금까지 알아낸 것.

    서버는 학습을 돌리지 않는다 — `scripts/daily.py` 가 남긴 기록을 읽기만 한다.
    """
    return learning.summary()


@router.get("/learning/defaults")
def learning_defaults(provider: str, symbol: str, timeframe: str = "1d") -> dict:
    """수동 학습 출발점. 자동 학습이 찾아 둔 설정이 있으면 거기서 시작한다."""
    found = learning.defaults(provider, symbol, timeframe)
    return {"config": found, "source": "champion" if found else "default"}


class LearnedBody(BaseModel):
    provider: str
    symbol: str
    timeframe: str = "1h"
    limit: int = Field(3000, ge=600, le=MAX_LIMIT)
    event_sources: list[str] | None = None


@router.post("/learned")
async def learned(body: LearnedBody) -> dict:
    """학습 모델(없으면 변동성 기준선)의 분위수 곡선."""
    from ..forecast.ml import model as ml_model

    name = model_name(body.provider, body.symbol, body.timeframe)
    if ml_model.load(name) is None:
        raise HTTPException(
            404,
            f"{body.symbol} {body.timeframe} 로 학습된 모델이 없다 — 먼저 학습해야 한다",
        )
    provider_info = _provider(body.provider).info
    sources = tuple(body.event_sources) if body.event_sources else events.DEFAULT_SOURCES
    data, _ = await _symbol_data(body.provider, body.symbol, body.timeframe, body.limit,
                                 provider_info.market, sources, True)
    result = await asyncio.to_thread(
        ml_model.predict, data.df, name, data.events, body.timeframe, data.attention
    )
    result["symbol"] = body.symbol
    return result
