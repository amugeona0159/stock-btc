"""관심도 — 사람들이 이 자산을 얼마나 찾아보는가.

원래 GDELT 보도량을 쓰려 했는데 API 가 막히는 망이 있다. Wikipedia 조회수 API 는
키가 필요 없고 2015년부터의 **일별** 이력을 준다. 검색·조회량을 시장 관심의 대리
변수로 쓰는 것은 오래된 방법이다.

한계는 분명하다:
- **일별뿐이다.** 시간봉에서는 그날 값을 그대로 깔아 쓴다. 장중 변화는 못 잡는다.
- 조회수는 '관심'이지 '방향'이 아니다. 급등에도 급락에도 같이 오른다.
- 문서 이름을 못 찾으면 그 종목은 관심도 축이 통째로 빈다.
"""
from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd

from ...providers.base import ProviderError
from ..schema import Event

API = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
       "/en.wikipedia/all-access/all-agents/{article}/daily/{start}/{end}")
# Wikimedia 는 연락처가 없는 User-Agent 를 403 으로 막는다. 정책이 그렇다 —
# 이름만 적은 UA("market-lens/0.1")도, 브라우저 흉내("Mozilla/5.0")도 거절당한다.
# 자기 저장소나 메일 주소로 바꿔 두는 게 예의다.
CONTACT = os.environ.get("MARKET_LENS_CONTACT", "https://github.com/local/market-lens")
HEADERS = {"User-Agent": f"market-lens/0.1 ({CONTACT})"}

# 심볼 -> 위키백과 문서. 여기 없는 것은 아래 규칙으로 만들어 본다.
ARTICLES = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "XRP": "Ripple_(payment_protocol)",
    "SOL": "Solana_(blockchain_platform)", "DOGE": "Dogecoin", "ADA": "Cardano_(blockchain_platform)",
    "BNB": "BNB_(cryptocurrency)", "LTC": "Litecoin", "TRX": "Tron_(blockchain)",
    "AAPL": "Apple_Inc.", "MSFT": "Microsoft", "NVDA": "Nvidia", "TSLA": "Tesla,_Inc.",
    "GOOGL": "Google", "AMZN": "Amazon_(company)", "META": "Meta_Platforms",
    "005930": "Samsung_Electronics", "005930.KS": "Samsung_Electronics",
    "000660": "SK_Hynix", "000660.KS": "SK_Hynix",
    "^GSPC": "S%26P_500", "^IXIC": "Nasdaq_Composite",
}

# 시세 심볼에서 떼어낼 꼬리표. BTCUSDT -> BTC, KRW-BTC -> BTC.
QUOTES = ("USDT", "USDC", "BUSD", "USD", "KRW", "BTC")

_CACHE: dict[tuple, tuple[float, pd.DataFrame]] = {}
CACHE_TTL = 6 * 3600.0
SPIKE_Z = 2.5


def article_for(symbol: str) -> str | None:
    """시세 심볼 → 위키백과 문서 이름. 못 찾으면 None (그 종목은 관심도 축이 빈다)."""
    raw = symbol.strip().upper()
    if raw in ARTICLES:
        return ARTICLES[raw]

    base = raw
    if "-" in base:                      # KRW-BTC
        base = base.split("-", 1)[1]
    for quote in QUOTES:
        if base.endswith(quote) and len(base) > len(quote):
            base = base[: -len(quote)]
            break
    base = base.split(".")[0]
    return ARTICLES.get(base)


async def views(article: str, start_ts: int, end_ts: int) -> pd.DataFrame:
    """일별 조회수. (ts, views) — ts 는 그날 00:00 UTC."""
    import httpx

    key = (article, start_ts // 86_400_000, end_ts // 86_400_000)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1].copy()

    stamp = lambda ms: pd.Timestamp(ms, unit="ms", tz="UTC").strftime("%Y%m%d")  # noqa: E731
    url = API.format(article=article, start=stamp(start_ts), end=stamp(end_ts))
    async with httpx.AsyncClient(timeout=20.0, headers=HEADERS) as client:
        try:
            res = await client.get(url)
            if res.status_code == 404:
                # 그런 문서가 없다. 오류가 아니라 '자료 없음' 이다.
                return pd.DataFrame(columns=["ts", "views"])
            res.raise_for_status()
            body = res.json()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"위키백과 조회수 요청 실패: {exc}") from exc

    rows = [
        {"ts": int(pd.Timestamp(item["timestamp"][:8]).tz_localize("UTC").timestamp() * 1000),
         "views": float(item["views"])}
        for item in body.get("items", [])
    ]
    frame = pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
    _CACHE[key] = (time.time(), frame)
    return frame.copy()


def _to_bars(frame: pd.DataFrame, ts: np.ndarray) -> pd.Series:
    """일별 값을 봉 시각에 맞춘다. 그날 값이 그날 안에서는 일정하다고 본다.

    **앞으로 채우기만 한다.** 뒤에서 당겨 오면 아직 나오지 않은 조회수를 쓰게 된다.
    """
    if frame.empty:
        return pd.Series(np.nan, index=range(len(ts)), dtype="float64")
    day = (ts // 86_400_000) * 86_400_000
    lookup = frame.set_index("ts")["views"]
    aligned = lookup.reindex(pd.Index(day)).to_numpy()
    return pd.Series(aligned).ffill()


def features(df: pd.DataFrame, frame: pd.DataFrame) -> pd.DataFrame:
    """봉마다의 관심도 축. 전부 무차원이고 [-1, 1] 안이다.

    수준(절대 조회수)이 아니라 **자기 과거 대비 어디쯤인지**를 쓴다. 위키백과 트래픽은
    해마다 추세가 있어서 절대값을 넣으면 모델이 연도를 외운다.
    """
    out = pd.DataFrame(
        {"attention_z": np.nan, "attention_change": np.nan, "attention_spike": 0.0},
        index=df.index, dtype="float64",
    )
    series = _to_bars(frame, df["ts"].to_numpy())
    if series.isna().all():
        return out

    log_views = np.log(series.clip(lower=1.0))
    # 90일 기준 z점수. 봉 단위가 아니라 날 단위로 보므로 창을 넉넉히 잡는다.
    step = float(np.median(np.diff(df["ts"].to_numpy()))) if len(df) > 1 else 86_400_000.0
    window = max(30, int(90 * 86_400_000 / step))
    mean = log_views.rolling(window, min_periods=window // 3).mean()
    std = log_views.rolling(window, min_periods=window // 3).std(ddof=0).replace(0.0, np.nan)
    z = (log_views - mean) / std

    out["attention_z"] = np.tanh(z / 2.0)
    out["attention_change"] = np.tanh(log_views.diff(max(1, int(86_400_000 / step))) / 0.3)
    out["attention_spike"] = (z >= SPIKE_Z).astype("float64") * 2.0 - 1.0
    return out


async def collect(df: pd.DataFrame, symbol: str) -> tuple[pd.DataFrame, str | None]:
    """(봉에 맞춘 관심도 축, 쓴 문서 이름). 문서를 못 찾으면 축은 비어 나간다."""
    article = article_for(symbol)
    if article is None or df.empty:
        return features(df, pd.DataFrame(columns=["ts", "views"])), None
    start = int(df["ts"].iloc[0])
    end = int(df["ts"].iloc[-1])
    try:
        frame = await views(article, start - 120 * 86_400_000, end)
    except ProviderError:
        frame = pd.DataFrame(columns=["ts", "views"])
    return features(df, frame), article


async def spikes(df: pd.DataFrame, symbol: str) -> list[Event]:
    """관심이 평소보다 크게 튄 날을 사건으로."""
    frame, article = await collect(df, symbol)
    if article is None:
        return []
    hits = frame["attention_spike"] > 0
    ts = df["ts"].to_numpy()
    out: list[Event] = []
    previous = -10**9
    for i in np.flatnonzero(hits.to_numpy()):
        # 같은 관심 급증이 며칠 이어지면 한 사건으로 접는다.
        if ts[i] - previous < 3 * 86_400_000:
            continue
        previous = int(ts[i])
        out.append(Event(
            ts=int(ts[i]), kind="news", title=f"'{article.replace('_', ' ')}' 관심 급증",
            source="attention", scope=f"symbol:{symbol.upper()}", severity=0.6,
            tags=("attention", "wikipedia"),
            note="위키백과 조회수가 90일 기준 +2.5시그마",
        ))
    return out


COLUMNS = ("attention_z", "attention_change", "attention_spike")
