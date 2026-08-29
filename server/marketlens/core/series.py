"""계산 결과를 화면이 그릴 수 있는 점 목록으로 편다.

시간 단위는 여기서 초로 바꾼다. lightweight-charts 가 UNIX 초를 받기 때문인데,
그 변환을 프론트에 두면 봉·지표·시그널이 각자 나눠 어긋난다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..indicators import catalog
from .timeframe import to_ms


@dataclass(frozen=True)
class IndicatorRequest:
    key: str
    params: dict
    id: str

    @staticmethod
    def parse(raw: dict, fallback_index: int = 0) -> "IndicatorRequest":
        key = raw.get("key")
        if not key:
            raise ValueError("지표 요청에 key 가 없다")
        params = raw.get("params") or {}
        return IndicatorRequest(key, params, raw.get("id") or f"{key}-{fallback_index}")


def candles_payload(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    return [
        {
            "time": int(row.ts // 1000),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": float(row.volume),
            "closed": bool(row.closed),
        }
        for row in df.itertuples()
    ]


def _points(ts_seconds: np.ndarray, values: np.ndarray) -> list[dict]:
    """값이 없는 자리는 **시각만** 보낸다(whitespace).

    버리면 안 된다. RSI(14) 는 첫 14봉이 비어 있는데 그걸 빼고 보내면 그 시리즈의
    첫 점이 캔들 14번째가 되고, lightweight-charts 의 logical 인덱스는 "그 차트의 첫
    데이터" 기준이라 **메인 차트의 0번과 보조 패널의 0번이 14봉 어긋난다.**
    그 상태로 같은 범위를 넘기면 보조 패널은 다른 구간을 보여 준다 — 차트를 확대해도
    RSI·MACD 가 안 맞던 이유가 이것이다. MACD 는 34봉이라 더 심하다.

    whitespace 점도 시간축 인덱스를 차지하므로(`wrapWhitespaceData`), 이렇게 보내면
    모든 시리즈가 첫 봉부터 같은 개수가 되어 인덱스가 저절로 맞는다.

    중간이 빈 자리도 whitespace 다 — 선이 끊긴다. 이어 그리면 없던 추세가 보인다.
    """
    finite = np.isfinite(values)
    if not finite.any():
        # 하나도 없으면 빈 배열. 화면이 이걸 보고 시리즈를 아예 안 만든다.
        return []
    return [
        {"time": int(t), "value": float(v)} if ok else {"time": int(t)}
        for t, v, ok in zip(ts_seconds, values, finite)
    ]


def compute_requests(
    df: pd.DataFrame, requests: list[IndicatorRequest], timeframe: str
) -> list[dict]:
    """요청한 지표들을 계산해 화면용 구조로. 하나가 터져도 나머지는 나간다."""
    step_seconds = to_ms(timeframe) // 1000
    base_ts = (df["ts"].to_numpy() // 1000).astype("int64") if not df.empty else np.array([], dtype="int64")

    results: list[dict] = []
    for request in requests:
        try:
            spec = catalog.get_spec(request.key)
            resolved = spec.resolve(request.params)
            frame = catalog.compute(request.key, df, request.params)
        except Exception as exc:  # 지표 하나의 실패가 차트 전체를 비우면 안 된다
            results.append({"id": request.id, "key": request.key, "error": str(exc)})
            continue

        outputs = []
        for out in spec.outputs:
            values = frame[out.key].to_numpy(dtype="float64")
            shift = out.shift_by(resolved)
            if shift:
                # 미래로 나가는 선(일목 선행스팬)은 마지막 봉 이후 구간만 보낸다.
                # 전 구간을 밀어 보내면 이미 present-aligned 로 나간 선과 겹쳐 두 번 그려진다.
                tail = shift + 1
                times = base_ts[-tail:] + shift * step_seconds
                values = values[-tail:]
            else:
                times = base_ts
            outputs.append({
                "key": out.key,
                "label": out.label,
                "draw": out.draw,
                "pane": out.pane,
                "color": out.color,
                "pair": out.pair,
                "optional": out.optional,
                "data": _points(times, values),
            })

        results.append({
            "id": request.id,
            "key": spec.key,
            "name": spec.name,
            "category": spec.category,
            "pane": spec.pane,
            "params": resolved,
            "formula": spec.formula,
            "outputs": outputs,
        })
    return results
