"""질문 해석과 조건부 예측.

규칙 파서는 LLM 이 없을 때의 폴백이자 LLM 결과의 비교 기준이다. 여기가 깨지면
키 없는 사람은 질문 기능을 통째로 못 쓴다.
"""
from __future__ import annotations

import numpy as np
import pytest

from marketlens.context import features as ctx
from marketlens.events.schema import Event, EventSet
from marketlens.scenario import engine, parser
from marketlens.scenario.schema import Scenario
from tests.conftest import make_candles


# --- 규칙 파서 --------------------------------------------------------------

@pytest.mark.parametrize("question,hours", [
    ("내일 어떻게 될까", 24),
    ("일주일 이내 차트", 168),
    ("한 달 뒤", 720),
    ("3일 동안", 72),
    ("5주 뒤에는", 840),
    ("12시간 뒤", 12),
])
def test_horizon_words(question, hours):
    assert parser.parse_rules(question, "1h").horizon_hours == pytest.approx(hours)


def test_horizon_defaults_to_a_week():
    """기간을 안 쓰면 되묻지 않고 1주일로 본다. 되물으면 한 번에 답이 안 나온다."""
    assert parser.parse_rules("어떻게 될까?", "1h").horizon_hours == 168


@pytest.mark.parametrize("question,tag", [
    ("금리 발표 뒤", "rate"),
    ("FOMC 이후", "rate"),
    ("규제 나오면", "regulation"),
    ("ETF 승인되면", "etf"),
    ("반감기 뒤", "halving"),
    ("해킹 터진 다음", "hack"),
    ("급락 나온 뒤", "spike"),
    ("거래량 터졌을 때", "volume"),
])
def test_event_words(question, tag):
    assert tag in parser.parse_rules(question, "1h").event_tags


def test_no_event_words_means_no_condition():
    """질문에 없는 조건을 만들어내면 안 된다."""
    draft = parser.parse_rules("일주일 뒤 어떻게 될까", "1h")
    assert draft.event_tags == [] and draft.event_kinds == []


def test_regime_words():
    assert parser.parse_rules("고변동 구간에서", "1h").require_volatility == 2
    assert parser.parse_rules("횡보장에서는", "1h").require_trend == 0
    assert parser.parse_rules("하락장에서 이틀", "1h").require_trend == -1


def test_event_condition_raises_context_weight():
    """사건 조건이 붙으면 상황 쪽을 더 본다 — 모양만 맞는 남의 사건을 덜 가져오게."""
    plain = parser.parse_rules("일주일 뒤", "1h")
    with_event = parser.parse_rules("금리 발표 뒤 일주일", "1h")
    assert with_event.context_weight > plain.context_weight


def test_interpretation_is_always_written():
    """사람이 해석을 보고 고칠 수 있어야 한다. 빈 해석은 검증 불가능이다."""
    for question in ("금리 발표 뒤 한 달", "아무 말", "급락"):
        assert parser.parse_rules(question, "1h").interpretation.strip()


# --- 기간을 봉 수로 -----------------------------------------------------------

def test_horizon_converts_by_timeframe():
    draft = parser.parse_rules("일주일 뒤", "1d")
    assert Scenario.from_draft(draft, "q", "1d", "rule").horizon == 7
    assert Scenario.from_draft(draft, "q", "1h", "rule").horizon == 168


def test_too_long_horizon_is_clamped_and_reported():
    """1시간봉으로 1년은 8760봉이다. 조용히 자르면 사용자는 1년을 본 줄 안다."""
    draft = parser.parse_rules("1년 뒤", "1h")
    plan = Scenario.from_draft(draft, "q", "1h", "rule")
    assert plan.horizon == 400
    assert any("잘랐다" in note for note in plan.notes)


# --- 조건 마스크 -------------------------------------------------------------

def _plan(**kwargs) -> Scenario:
    base = dict(question="q", timeframe="1h", horizon=12,
                horizon_hours=12.0, horizon_text="12시간")
    return Scenario(**{**base, **kwargs})


def test_no_condition_means_no_mask(candles):
    mask, notes = engine.build_mask(candles, _plan(), [])
    assert mask is None and notes == []


def test_event_condition_narrows_the_mask(candles):
    events = [Event(ts=int(candles["ts"].iloc[i]), kind="macro", title="e", source="seed")
              for i in range(100, 300, 2)]
    mask, notes = engine.build_mask(candles, _plan(event_kinds=("macro",)), events)
    assert mask is not None
    assert mask.sum() < len(candles)
    assert any("좁혔다" in n for n in notes)


def test_missing_events_are_reported_not_hidden(candles):
    """조건에 맞는 사건이 없으면 조용히 무시하면 안 된다.

    조용히 넘어가면 사용자는 조건이 먹은 결과라고 믿는다 — 이게 제일 위험한 거짓말이다.
    """
    mask, notes = engine.build_mask(candles, _plan(event_tags=("없는태그",)), [])
    assert any("찾지 못했다" in n for n in notes)


def test_over_narrow_conditions_are_relaxed_with_a_note(candles):
    events = [Event(ts=int(candles["ts"].iloc[150]), kind="macro", title="e", source="seed")]
    mask, notes = engine.build_mask(
        candles, _plan(event_kinds=("macro",), require_volatility=2), events
    )
    assert notes, "조건을 풀었으면 반드시 알려야 한다"


def test_select_events_filters_by_tag():
    events = EventSet().add(
        Event(ts=1, kind="macro", title="금리", source="seed", tags=("rate",)),
        Event(ts=2, kind="macro", title="물가", source="seed", tags=("inflation",)),
    )
    picked = engine.select_events(events, _plan(event_tags=("rate",)), "BTCUSDT", "crypto")
    assert [e.title for e in picked] == ["금리"]


# --- 전체 흐름 --------------------------------------------------------------

def test_run_produces_an_answer(candles):
    result = engine.run(candles, "TEST", "crypto", _plan(), EventSet(), window=32, top_k=10)
    assert result["answer"]
    assert result["projection"]["available"]
    assert result["scenario"]["horizonText"] == "12시간"
    assert result["citations"]


def test_run_survives_short_history():
    tiny = make_candles(count=40)
    result = engine.run(tiny, "TEST", "crypto", _plan(), EventSet(), window=32)
    assert result["projection"]["available"] is False
    assert result["answer"]


# --- 상황 벡터 --------------------------------------------------------------

def test_context_axes_stay_bounded(candles):
    """축 하나만 범위가 크면 그 축이 거리를 독점한다."""
    vector = ctx.build(candles).dropna()
    assert not vector.empty
    for axis in ctx.AXES:
        values = vector[axis].to_numpy()
        assert np.abs(values).max() <= 1.5, f"{axis} 가 범위를 넘었다: {values.max()}"


def test_context_is_scale_invariant(candles):
    """가격을 1000배 해도 상황 벡터는 거의 같아야 한다 — 무차원이 아니면 아니다."""
    scaled = candles.copy()
    for column in ("open", "high", "low", "close"):
        scaled[column] = scaled[column] * 1000.0
    a = ctx.build(candles).iloc[-1]
    b = ctx.build(scaled).iloc[-1]
    for axis in ctx.AXES:
        if np.isfinite(a[axis]) and np.isfinite(b[axis]):
            assert a[axis] == pytest.approx(b[axis], abs=1e-6), axis


def test_group_weights_sum_by_group():
    """캘린더 축이 여덟 개라고 자동으로 무거워지면 안 된다."""
    weights = ctx.weights()
    start = 0
    totals = {}
    for group in ctx.GROUPS:
        count = len(ctx.GROUP_AXES[group.key])
        totals[group.key] = weights[start:start + count].sum()
        start += count
    assert totals["calendar"] == pytest.approx(0.4)
    assert totals["trend"] == pytest.approx(1.4)
