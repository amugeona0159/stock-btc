"""HTTP 엔드포인트.

지표 계산은 전부 여기를 지난다. 프론트는 받은 시리즈를 그리기만 한다 — 양쪽에서 계산하면
반올림과 시드값 차이로 같은 화면 안의 20EMA 두 개가 어긋난다.
"""
from __future__ import annotations

import asyncio

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import forecast as forecast_layer
from ..backtest import engine as backtest_engine
from ..core.series import IndicatorRequest, candles_payload, compute_requests
from ..indicators import catalog, patterns
from ..providers import ProviderError, ProviderUnavailable, describe, get as get_provider
from ..signals.engine import evaluate
from ..store.cache import cache

router = APIRouter(prefix="/api")

MAX_LIMIT = 5000


async def load_candles(provider_key: str, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    provider = _provider(provider_key)
    if not provider.supports(timeframe):
        raise HTTPException(400, f"{provider.info.name} 은 {timeframe} 봉을 주지 않는다")

    cached = cache.get(provider_key, symbol, timeframe, limit)
    if cached is not None:
        return cached
    try:
        df = await provider.history(symbol, timeframe, limit)
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
    signal, prediction = await asyncio.gather(
        asyncio.to_thread(evaluate, df),
        asyncio.to_thread(forecast_layer.combined, df, body.timeframe, body.horizon, body.model),
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


class TrainBody(BaseModel):
    provider: str
    symbol: str
    timeframe: str = "1d"
    limit: int = Field(2000, ge=300, le=MAX_LIMIT)
    horizon: int = Field(10, ge=1, le=100)


@router.post("/train")
async def train(body: TrainBody) -> dict:
    from ..forecast.ml import model as ml_model

    df = await load_candles(body.provider, body.symbol, body.timeframe, body.limit)
    name = f"{body.provider}-{body.symbol}-{body.timeframe}".lower()
    try:
        report = await asyncio.to_thread(ml_model.train, df, name, body.horizon)
    except ml_model.MissingDependency as exc:
        raise HTTPException(501, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"model": name, "report": report}
