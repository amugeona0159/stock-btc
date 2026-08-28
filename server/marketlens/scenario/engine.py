"""시나리오 → 예측.

질문의 조건으로 **후보 구간을 좁힌 뒤** 유사구간을 찾는다. "규제 뉴스가 났을 때"를
물으면 규제 뉴스가 있었던 자리만 후보가 되고, 그 이후 실제 경로가 답이 된다.

조건이 너무 빡빡하면 사례가 없다. 그때 조용히 조건을 무시하면 사용자는 조건이 먹은 줄
안다 — 그래서 단계적으로 풀되 **무엇을 풀었는지 반드시 적는다**(`notes`).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..analog import matcher, projection
from ..context import features as ctx
from ..context import regime as reg
from ..core.candle import closed_only
from ..core.timeframe import to_ms
from ..events import study as event_study
from ..events.schema import Event, EventSet
from ..research import registry as research
from .schema import Scenario

# 조건에 맞는 후보가 이보다 적으면 조건을 한 단계 푼다.
MIN_CANDIDATES = 60
# 사건이 일어난 뒤 이 봉 수까지를 "그 사건의 자리" 로 본다.
EVENT_LAG = 2


def _event_mask(df: pd.DataFrame, events: list[Event], lag: int = EVENT_LAG) -> np.ndarray:
    """사건이 난 자리(창이 끝날 위치)를 True 로."""
    ts_values = df["ts"].to_numpy()
    mask = np.zeros(len(df), dtype=bool)
    for event in events:
        index = int(np.searchsorted(ts_values, event.ts, side="left"))
        if index >= len(mask):
            continue
        mask[index : min(len(mask), index + lag + 1)] = True
    return mask


def _regime_mask(df: pd.DataFrame, volatility: int | None, trend: int | None) -> np.ndarray:
    if volatility is None and trend is None:
        return np.ones(len(df), dtype=bool)
    states = reg.frame(df)
    mask = np.ones(len(df), dtype=bool)
    if volatility is not None:
        mask &= (states["volatility"] == float(volatility)).fillna(False).to_numpy()
    if trend is not None:
        mask &= (states["trend"] == float(trend)).fillna(False).to_numpy()
    return mask


def select_events(events: EventSet, scenario: Scenario, symbol: str, market: str) -> list[Event]:
    """시나리오 조건에 맞는 사건만."""
    candidates = events.for_symbol(symbol, market)
    if not scenario.event_kinds and not scenario.event_tags:
        return []
    wanted_kinds = set(scenario.event_kinds)
    wanted_tags = set(scenario.event_tags)
    return [
        e for e in candidates
        if (not wanted_kinds or e.kind in wanted_kinds)
        and (not wanted_tags or wanted_tags & set(e.tags))
    ]


def build_mask(
    df: pd.DataFrame, scenario: Scenario, matched_events: list[Event]
) -> tuple[np.ndarray | None, list[str]]:
    """후보 마스크와, 조건을 어떻게 적용/완화했는지의 기록."""
    notes: list[str] = []
    wants_event = bool(scenario.event_kinds or scenario.event_tags)
    wants_regime = scenario.require_volatility is not None or scenario.require_trend is not None

    if not wants_event and not wants_regime:
        return None, notes

    event_mask = _event_mask(df, matched_events) if wants_event else np.ones(len(df), dtype=bool)
    regime_mask = _regime_mask(df, scenario.require_volatility, scenario.require_trend)

    if wants_event and not matched_events:
        notes.append("조건에 맞는 사건을 과거에서 찾지 못했다 — 사건 조건을 빼고 찾았다")
        event_mask = np.ones(len(df), dtype=bool)

    combined = event_mask & regime_mask
    if combined.sum() >= MIN_CANDIDATES:
        if wants_event and matched_events:
            notes.append(f"사건 {len(matched_events)}건이 걸린 자리로 후보를 좁혔다")
        if wants_regime:
            notes.append("레짐 조건을 걸었다")
        return combined, notes

    # 단계적으로 푼다. 무엇을 풀었는지 반드시 남긴다.
    if wants_event and event_mask.sum() >= MIN_CANDIDATES:
        notes.append(
            f"사건+레짐 조건을 다 만족하는 자리가 {int(combined.sum())}곳뿐이라 "
            "레짐 조건을 풀었다"
        )
        return event_mask, notes
    if wants_regime and regime_mask.sum() >= MIN_CANDIDATES:
        notes.append(
            f"사건 조건에 맞는 자리가 {int(event_mask.sum())}곳뿐이라 사건 조건을 풀고 "
            "레짐 조건만 걸었다"
        )
        return regime_mask, notes

    notes.append(
        f"조건에 맞는 과거가 {int(combined.sum())}곳뿐이다 — 조건을 모두 풀고 "
        "모양이 비슷한 구간으로만 찾았다. 아래 숫자를 조건부 결과로 읽지 말 것."
    )
    return None, notes


def _event_path(car_profile: dict, last_close: float, last_ts: int, timeframe: str,
                horizon: int) -> list[dict] | None:
    """이벤트 스터디의 평균 CAR 을 현재 가격에 붙인 경로."""
    if not car_profile.get("available"):
        return None
    before = car_profile["before"]
    car = car_profile["meanCar"][before:]        # 사건 시점부터
    if len(car) < 2:
        return None
    step = to_ms(timeframe) // 1000
    base = last_ts // 1000
    anchor = car[0]
    points = []
    for h in range(min(horizon + 1, len(car))):
        # CAR 은 퍼센트다. 사건 시점을 0으로 맞춰 현재 가격에 얹는다.
        points.append({"time": int(base + step * h),
                       "value": last_close * (1.0 + (car[h] - anchor) / 100.0)})
    return points


def summarize(scenario: Scenario, forecast: dict, car: dict, matched: int) -> str:
    """숫자를 사람 문장으로. 단정하지 않는다 — 근거의 크기를 같이 말한다."""
    if not forecast.get("available"):
        return f"{scenario.horizon_text} 뒤를 볼 만한 과거 사례를 찾지 못했다."

    move = forecast["expectedMovePct"]
    prob = forecast["probUp"] * 100
    count = forecast["sampleCount"]
    reliable = forecast["diagnostics"]["reliable"]

    parts = [
        f"비슷했던 과거 {count}건을 기준으로 {scenario.horizon_text} 뒤 중앙값은 "
        f"{move:+.2f}%, 상승 쪽이 {prob:.0f}% 다."
    ]
    if scenario.event_kinds or scenario.event_tags:
        if matched:
            parts.append(f"조건에 맞는 사건 {matched}건이 후보를 좁혔다.")
        else:
            parts.append("다만 조건에 맞는 사건이 과거에 없어 사건 조건은 적용되지 않았다.")
    if car.get("available"):
        sign = "위" if car["finalCarPct"] > 0 else "아래"
        verdict = "통계적으로 유의하다" if car["significant"] else "통계적으로 유의하지 않다"
        parts.append(
            f"같은 종류의 사건 {car['count']}건에서는 이후 평균이 정상 수익률보다 "
            f"{abs(car['finalCarPct']):.2f}% {sign}였고, 그 차이는 {verdict}(t={car['finalTStat']:+.2f})."
        )
    if not reliable:
        parts.append("사례가 적거나 흩어져 있어 이 숫자는 참고 수준으로만 볼 것.")
    return " ".join(parts)


def run(
    df: pd.DataFrame,
    symbol: str,
    market: str,
    scenario: Scenario,
    events: EventSet,
    window: int = 48,
    top_k: int = 20,
) -> dict:
    closed = closed_only(df).reset_index(drop=True)
    matched_events = select_events(events, scenario, symbol, market)
    mask, notes = build_mask(closed, scenario, matched_events)

    series = matcher.Series(f"{symbol}-{scenario.timeframe}", closed, mask=mask)
    matches = matcher.search(
        closed, [series],
        window=window, horizon=scenario.horizon, top_k=top_k,
        context_weight=scenario.context_weight,
        group_weights=scenario.group_weights or None,
    )
    forecast = projection.project(closed, matches, scenario.horizon, scenario.timeframe)

    car = event_study.aggregate(
        closed, matched_events,
        before=max(2, min(10, scenario.horizon // 4)),
        after=scenario.horizon,
        label=" · ".join(scenario.event_tags or scenario.event_kinds) or "조건 사건",
    ) if matched_events else {"available": False, "reason": "조건에 맞는 사건이 없다"}

    event_path = None
    if forecast.get("available"):
        event_path = _event_path(car, forecast["last"], forecast["lastTs"],
                                 scenario.timeframe, scenario.horizon)

    situation = ctx.describe(closed)
    return {
        "scenario": {**scenario.to_dict(), "notes": scenario.notes + notes},
        "situation": situation,
        "projection": forecast,
        "eventStudy": car,
        "eventPath": event_path,
        "matchedEvents": [e.to_dict() for e in matched_events[-40:]],
        "matches": [m.to_dict() for m in matches],
        "answer": summarize(scenario, forecast, car, len(matched_events)),
        "citations": research.cite(
            "analog_retrieval_forecast", "event_study_car", "conformal_intervals",
            "technical_pattern_information", "calendar_effects",
        ),
    }
