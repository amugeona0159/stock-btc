"""사례를 예측으로 바꾼다.

찾아낸 과거 사례들의 이후 경로를 현재 가격에 붙여 앞으로 그린다. 경로 하나하나를
그대로 보여주는 것과(그때 실제로 이런 일이 있었다), 그것들의 분포를 밴드로 보여주는 것을
**같이** 낸다 — 경로만 보면 그럴듯한 한 줄에 눈이 가고, 밴드만 보면 무슨 일이 있었는지가 사라진다.

밴드를 그대로 믿지 않기 위한 장치가 있다: 사례 하나를 빼고 나머지로 만든 밴드가
그 사례를 실제로 덮었는지 세어(`coverage`), 목표 커버리지에 못 미치면 밴드를 넓힌다.
컨포멀 예측의 아이디어를 사례 표본에 그대로 적용한 것이다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..core.timeframe import to_ms
from ..research import registry as research
from .matcher import Match

# 화면에 겹쳐 그릴 개별 경로 수. 이보다 많으면 밴드가 안 보인다.
MAX_PATHS = 10
QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)
# 밴드 신뢰 검사에 쓰는 명목 커버리지 (p10~p90).
NOMINAL_COVERAGE = 0.8
# 넓히기 배수의 상한. 사례가 몇 건 없을 때 밴드가 무한정 커지는 걸 막는다.
MAX_WIDEN = 3.0


def _weights(matches: list[Match]) -> np.ndarray:
    """거리가 가까운 사례에 더 큰 비중. 중앙 거리를 척도로 써서 자산·기간에 안 묶이게 한다."""
    distances = np.array([m.distance for m in matches], dtype="float64")
    scale = float(np.median(distances))
    if not np.isfinite(scale) or scale <= 0:
        return np.ones(len(matches), dtype="float64") / max(1, len(matches))
    weight = np.exp(-distances / scale)
    return weight / weight.sum()


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    total = w.sum()
    if total <= 0:
        return float(np.nan)
    # 각 표본이 자기 폭의 한가운데를 차지한다고 본다. 표본이 적을 때 끝값으로 쏠리지 않는다.
    cumulative = (np.cumsum(w) - 0.5 * w) / total
    return float(np.interp(q, cumulative, v))


def _band_matrix(matches: list[Match], horizon: int) -> np.ndarray:
    """(사례 수, horizon+1) 누적 로그수익률 행렬."""
    rows = []
    for match in matches:
        path = match.path
        if len(path) < horizon + 1:
            path = np.concatenate([path, np.full(horizon + 1 - len(path), path[-1])])
        rows.append(path[: horizon + 1])
    return np.vstack(rows)


def _coverage(paths: np.ndarray, weights: np.ndarray) -> float:
    """사례 하나를 빼고 만든 밴드가 그 사례의 최종값을 덮은 비율.

    사례가 서로 비슷하기만 하고 실제 분포를 못 담고 있으면 여기서 낮게 나온다.
    """
    finals = paths[:, -1]
    if len(finals) < 5:
        return float("nan")
    inside = 0
    lo_q, hi_q = (1 - NOMINAL_COVERAGE) / 2, 1 - (1 - NOMINAL_COVERAGE) / 2
    for i in range(len(finals)):
        mask = np.ones(len(finals), dtype=bool)
        mask[i] = False
        others, other_weights = finals[mask], weights[mask]
        low = weighted_quantile(others, other_weights, lo_q)
        high = weighted_quantile(others, other_weights, hi_q)
        inside += int(low <= finals[i] <= high)
    return inside / len(finals)


def project(
    df: pd.DataFrame,
    matches: list[Match],
    horizon: int,
    timeframe: str,
    max_paths: int = MAX_PATHS,
) -> dict:
    """사례 → 화면에 그릴 예측."""
    if not matches or df.empty:
        return {"available": False, "reason": "비슷했던 구간을 찾지 못했다"}

    last_close = float(df["close"].iloc[-1])
    last_ts = int(df["ts"].iloc[-1])
    step = to_ms(timeframe) // 1000
    times = [int(last_ts // 1000 + step * h) for h in range(horizon + 1)]

    paths = _band_matrix(matches, horizon)
    weights = _weights(matches)

    coverage = _coverage(paths, weights)
    # 명목보다 덜 덮었으면 그만큼 넓힌다. 넘치게 덮었다고 좁히지는 않는다 —
    # 사례가 적을 때 우연히 높게 나온 커버리지로 밴드를 조이면 위험하다.
    widen = 1.0
    if np.isfinite(coverage) and 0 < coverage < NOMINAL_COVERAGE:
        widen = min(MAX_WIDEN, NOMINAL_COVERAGE / coverage)

    bands: dict[str, list[dict]] = {f"p{int(q * 100)}": [] for q in QUANTILES}
    median_series: list[float] = []
    for h in range(horizon + 1):
        column = paths[:, h]
        centre = weighted_quantile(column, weights, 0.5)
        median_series.append(centre)
        for q in QUANTILES:
            value = weighted_quantile(column, weights, q)
            spread = (value - centre) * widen
            price = last_close * float(np.exp(centre + spread))
            bands[f"p{int(q * 100)}"].append({"time": times[h], "value": price})

    ranked = sorted(range(len(matches)), key=lambda i: matches[i].distance)[:max_paths]
    drawn = [
        {
            "id": f"{matches[i].source}@{matches[i].ts}",
            "source": matches[i].source,
            "ts": matches[i].ts,
            "distance": round(matches[i].distance, 4),
            "weight": round(float(weights[i]), 4),
            "outcome": round(matches[i].outcome, 6),
            "windowStartTs": matches[i].window_start_ts,
            "windowEndTs": matches[i].window_end_ts,
            "points": [
                {"time": times[h], "value": last_close * float(np.exp(paths[i, h]))}
                for h in range(horizon + 1)
            ],
        }
        for i in ranked
    ]

    finals = paths[:, -1]
    prob_up = float((weights * (finals > 0)).sum())
    distances = np.array([m.distance for m in matches])

    return {
        "available": True,
        "horizon": horizon,
        "timeframe": timeframe,
        "last": last_close,
        "lastTs": last_ts,
        "targetTs": last_ts + to_ms(timeframe) * horizon,
        "sampleCount": len(matches),
        "paths": drawn,
        "bands": bands,
        "median": last_close * float(np.exp(median_series[-1])),
        "expectedMovePct": float(np.expm1(median_series[-1]) * 100.0),
        "probUp": round(prob_up, 4),
        "diagnostics": {
            "coverage": None if not np.isfinite(coverage) else round(coverage, 3),
            "nominalCoverage": NOMINAL_COVERAGE,
            "widenFactor": round(widen, 3),
            "distanceMin": round(float(distances.min()), 4),
            "distanceMedian": round(float(np.median(distances)), 4),
            "distanceMax": round(float(distances.max()), 4),
            # 사례가 적으면 아래 숫자를 그대로 믿으면 안 된다. 화면이 이 값을 경고에 쓴다.
            "reliable": bool(len(matches) >= 10 and (not np.isfinite(coverage)
                                                     or coverage >= 0.5)),
        },
        "citations": research.cite(
            "analog_retrieval_forecast", "analog_dtw", "analog_znorm",
            "conformal_intervals", "technical_pattern_information",
        ),
    }
