"""이벤트 스터디.

"이슈가 터졌을 때 차트가 어떻게 움직였나" 를 재는 표준 절차다
(research.library: event_study_car).

절차는 셋뿐이다:
1. 사건 **전** 구간(추정창)에서 정상 수익률을 잡는다.
2. 사건 **주변** 구간(사건창)에서 실제 수익률과의 차이를 낸다 = 초과수익률(AR).
3. 그걸 누적한다 = CAR.

추정창과 사건창 사이에 틈(gap)을 둔다. 정보가 미리 새서 사건 직전부터 움직였다면,
그 움직임이 '정상'으로 추정되어 효과가 통째로 사라진다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..core.candle import closed_only
from ..research import registry as research
from .schema import Event

ESTIMATION = 120     # 정상 수익률을 재는 창 길이
GAP = 5              # 추정창과 사건창 사이의 틈
MIN_EVENTS = 3       # 이보다 적으면 평균을 내지 않는다


@dataclass
class EventWindow:
    event: Event
    index: int
    abnormal: np.ndarray      # 사건창의 초과수익률 (길이 before+after+1)
    car: np.ndarray           # 그 누적
    sigma: float              # 추정창의 수익률 표준편차


def _locate(ts_values: np.ndarray, event_ts: int) -> int | None:
    """사건 시각 **이후 첫 봉**. 사건이 봉 한가운데 일어나면 그 봉은 이미 반응이 섞여 있다."""
    position = int(np.searchsorted(ts_values, event_ts, side="left"))
    return position if 0 <= position < len(ts_values) else None


def windows(
    df: pd.DataFrame,
    events: list[Event],
    before: int = 5,
    after: int = 20,
    estimation: int = ESTIMATION,
    gap: int = GAP,
) -> list[EventWindow]:
    closed = closed_only(df).reset_index(drop=True)
    if len(closed) < estimation + gap + before + after + 2:
        return []

    ts_values = closed["ts"].to_numpy()
    returns = np.log(closed["close"] / closed["close"].shift(1)).to_numpy()

    out: list[EventWindow] = []
    for event in events:
        index = _locate(ts_values, event.ts)
        if index is None:
            continue
        est_end = index - before - gap
        est_start = est_end - estimation
        if est_start < 1 or index + after >= len(closed):
            continue

        sample = returns[est_start:est_end]
        sample = sample[np.isfinite(sample)]
        if len(sample) < estimation // 2:
            continue
        # 평균조정 모형. 시장 지수를 안 쓰는 이유는 프로바이더마다 지수가 다르고,
        # 없는 시장(암호화폐)에서 억지 지수를 만들면 그게 더 큰 오차가 된다.
        mu = float(sample.mean())
        sigma = float(sample.std(ddof=1))

        window = returns[index - before : index + after + 1]
        if len(window) != before + after + 1 or not np.all(np.isfinite(window)):
            continue
        abnormal = window - mu
        out.append(EventWindow(event, index, abnormal, np.cumsum(abnormal), sigma))
    return out


def aggregate(
    df: pd.DataFrame,
    events: list[Event],
    before: int = 5,
    after: int = 20,
    label: str = "",
) -> dict:
    """사건들을 정렬해 평균 AR·CAR 를 낸다."""
    collected = windows(df, events, before, after)
    if len(collected) < MIN_EVENTS:
        return {
            "available": False,
            "reason": f"사건이 {len(collected)}건뿐이다 — 최소 {MIN_EVENTS}건은 있어야 평균이 의미를 갖는다",
            "count": len(collected),
            "label": label,
        }

    car_matrix = np.vstack([w.car for w in collected])
    ar_matrix = np.vstack([w.abnormal for w in collected])
    offsets = list(range(-before, after + 1))

    mean_car = car_matrix.mean(axis=0)
    std_car = car_matrix.std(axis=0, ddof=1)
    n = len(collected)
    # 횡단면 t 값. 사건창이 겹치면 독립성이 깨져 이 값이 부풀려진다 - 화면이 그 경고를 같이 낸다.
    t_stat = np.divide(mean_car, std_car / np.sqrt(n),
                       out=np.zeros_like(mean_car), where=std_car > 0)

    finals = car_matrix[:, -1]
    overlapping = _has_overlap(collected, before + after)

    return {
        "available": True,
        "label": label,
        "count": n,
        "before": before,
        "after": after,
        "offsets": offsets,
        # 로그 초과수익률을 퍼센트로 편다. 화면이 바로 그린다.
        "meanAr": [round(float(np.expm1(v) * 100), 4) for v in ar_matrix.mean(axis=0)],
        "meanCar": [round(float(np.expm1(v) * 100), 4) for v in mean_car],
        "medianCar": [round(float(np.expm1(v) * 100), 4) for v in np.median(car_matrix, axis=0)],
        "carLow": [round(float(np.expm1(v) * 100), 4) for v in np.percentile(car_matrix, 25, axis=0)],
        "carHigh": [round(float(np.expm1(v) * 100), 4) for v in np.percentile(car_matrix, 75, axis=0)],
        "tStat": [round(float(v), 3) for v in t_stat],
        "finalCarPct": round(float(np.expm1(mean_car[-1]) * 100), 4),
        "finalTStat": round(float(t_stat[-1]), 3),
        # 방향이 얼마나 일관됐나. 평균만 보면 한 건이 끌어올린 것을 못 본다.
        "hitRate": round(float((finals > 0).mean()), 3),
        "significant": bool(abs(t_stat[-1]) >= 1.96),
        "overlapping": overlapping,
        "events": [
            {
                **w.event.to_dict(),
                "carPct": round(float(np.expm1(w.car[-1]) * 100), 4),
                "immediatePct": round(float(np.expm1(w.abnormal[before]) * 100), 4),
            }
            for w in collected
        ],
        "citations": research.cite("event_study_car", "crypto_announcement_reaction"),
    }


def _has_overlap(collected: list[EventWindow], span: int) -> bool:
    positions = sorted(w.index for w in collected)
    return any(b - a < span for a, b in zip(positions, positions[1:]))


def by_group(
    df: pd.DataFrame,
    events: list[Event],
    key: str = "kind",
    before: int = 5,
    after: int = 20,
) -> list[dict]:
    """종류·태그별로 나눠 비교한다. '어떤 종류의 이슈가 실제로 움직였나' 가 여기서 나온다."""
    buckets: dict[str, list[Event]] = {}
    for event in events:
        if key == "tag":
            for tag in event.tags or ("(무태그)",):
                buckets.setdefault(tag, []).append(event)
        else:
            buckets.setdefault(getattr(event, key, "?"), []).append(event)

    results = [aggregate(df, group, before, after, label) for label, group in buckets.items()]
    usable = [r for r in results if r["available"]]
    usable.sort(key=lambda r: -abs(r["finalCarPct"]))
    return usable + [r for r in results if not r["available"]]
