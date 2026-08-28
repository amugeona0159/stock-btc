"""FRED - 매크로 지표. 키 없이 CSV 엔드포인트로 받는다.

`fredgraph.csv` 는 API 키를 요구하지 않는다. 정식 REST API 는 키가 필요하지만,
이 프로그램은 시계열만 있으면 되므로 키 없이 도는 쪽을 쓴다.

여기서 사건이 되는 것은 **값이 바뀐 날**이다. 정책금리가 움직인 날, 물가가 크게 튄 날처럼
발표 자체가 시장을 흔든 자리다.
"""
from __future__ import annotations

import io
import time

import numpy as np
import pandas as pd

from ...providers.base import ProviderError
from ..schema import Event

CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# 시리즈 -> (한글 이름, 임계, 태그, 단위, 변화를 %로 볼지)
# 지수(CPI·연준 총자산)는 포인트 변화를 사람이 못 읽는다. 전월 대비 %로 바꿔 보여준다.
SERIES = {
    "DFF":      ("연방기금 실효금리", 0.10, ("monetary", "rate"), "%p", False),
    "DGS10":    ("미국 10년물 금리", 0.15, ("rate", "bond"), "%p", False),
    "T10Y2Y":   ("장단기 금리차(10년-2년)", 0.15, ("rate", "curve"), "%p", False),
    "VIXCLS":   ("VIX 변동성지수", 5.00, ("volatility", "risk"), "", False),
    "CPIAUCSL": ("소비자물가", 0.30, ("inflation",), "%", True),
    "UNRATE":   ("실업률", 0.20, ("labor",), "%p", False),
    "WALCL":    ("연준 총자산", 1.0, ("liquidity", "balance-sheet"), "%", True),
}


# 매크로 지표는 하루에 한 번 갱신되면 많이 갱신되는 것이다. 질문할 때마다 일곱 개
# 시리즈를 다시 받으면 그 왕복이 응답 시간의 대부분을 차지한다.
_CACHE: dict[tuple, tuple[float, pd.DataFrame]] = {}
CACHE_TTL = 3600.0


async def series(series_id: str, start_ts: int, end_ts: int) -> pd.DataFrame:
    import httpx

    # 요청 범위는 날짜 단위로만 의미가 있다. 밀리초까지 키에 넣으면 캐시가 절대 안 맞는다.
    cache_key = (series_id, start_ts // 86_400_000, end_ts // 86_400_000)
    hit = _CACHE.get(cache_key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1].copy()

    params = {
        "id": series_id,
        "cosd": pd.Timestamp(start_ts, unit="ms", tz="UTC").strftime("%Y-%m-%d"),
        "coed": pd.Timestamp(end_ts, unit="ms", tz="UTC").strftime("%Y-%m-%d"),
    }
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            res = await client.get(CSV, params=params)
            res.raise_for_status()
        except Exception as exc:
            raise ProviderError(f"FRED {series_id} 요청 실패: {exc}") from exc

    frame = pd.read_csv(io.StringIO(res.text))
    if frame.shape[1] < 2:
        return pd.DataFrame(columns=["ts", "value"])
    date_column, value_column = frame.columns[0], frame.columns[1]
    # pandas 3 은 파싱 결과의 해상도를 스스로 정한다(초·마이크로초가 섞여 나온다).
    # 단위를 ms 로 못박은 뒤 정수로 바꿔야 1970년대로 떨어지지 않는다.
    frame["ts"] = (pd.to_datetime(frame[date_column], utc=True)
                   .astype("datetime64[ms, UTC]").astype("int64"))
    frame["value"] = pd.to_numeric(frame[value_column], errors="coerce")
    result = frame[["ts", "value"]].dropna().reset_index(drop=True)
    _CACHE[cache_key] = (time.time(), result)
    return result.copy()


async def changes(series_id: str, start_ts: int, end_ts: int) -> list[Event]:
    """값이 임계 이상 바뀐 날을 사건으로."""
    if series_id not in SERIES:
        raise ProviderError(f"등록되지 않은 FRED 시리즈: {series_id} ({sorted(SERIES)})")
    label, threshold, tags, unit, as_pct = SERIES[series_id]

    data = await series(series_id, start_ts, end_ts)
    if len(data) < 3:
        return []

    if as_pct:
        delta = data["value"].pct_change() * 100.0
    else:
        delta = data["value"].diff()
    hits = delta.abs() >= threshold
    # fredgraph.csv 는 월별 시리즈에서 cosd/coed 를 무시하고 전 기간을 돌려준다.
    # 그래서 여기서 다시 자른다 - 변화량은 자르기 전에 계산해야 경계의 첫 변화를 안 잃는다.
    in_range = (data["ts"] >= start_ts) & (data["ts"] <= end_ts)
    hits = hits & in_range
    out: list[Event] = []
    for i in np.flatnonzero(hits.fillna(False).to_numpy()):
        change = float(delta.iloc[i])
        suffix = "" if as_pct else f" (={data['value'].iloc[i]:.2f})"
        out.append(Event(
            ts=int(data["ts"].iloc[i]),
            kind="macro",
            title=f"{label} {change:+.2f}{unit}{suffix}",
            source="fred",
            scope="global",
            severity=float(np.clip(abs(change) / (threshold * 4), 0.2, 1.0)),
            tags=tags,
            note=f"FRED {series_id}",
        ))
    return out


async def collect(start_ts: int, end_ts: int, ids: tuple[str, ...] | None = None) -> list[Event]:
    """여러 시리즈를 한 번에. 하나가 실패해도 나머지는 나간다."""
    out: list[Event] = []
    for series_id in (ids or tuple(SERIES)):
        try:
            out.extend(await changes(series_id, start_ts, end_ts))
        except ProviderError:
            continue
    return sorted(out, key=lambda e: e.ts)
