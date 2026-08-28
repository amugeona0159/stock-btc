"""사건 탐지와 이벤트 스터디."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from marketlens.core.candle import to_frame
from marketlens.events import catalog, detectors, study
from marketlens.events.schema import Event, EventSet
from tests.conftest import make_candles


def _with_crash(bars: int = 400, at: int = 300, size: float = -0.18) -> pd.DataFrame:
    """한 봉만 크게 떨어뜨린 데이터. 탐지기가 이걸 못 잡으면 아무 소용이 없다."""
    df = make_candles(count=bars, seed=7).copy()
    rows = df.to_dict("records")
    prev = rows[at - 1]["close"]
    price = prev * (1 + size)
    rows[at].update({
        "open": prev, "high": prev, "low": price, "close": price,
        "volume": rows[at]["volume"] * 12,
    })
    for i in range(at + 1, bars):
        for key in ("open", "high", "low", "close"):
            rows[i][key] *= (1 + size)
    return to_frame(rows)


# --- 탐지 ------------------------------------------------------------------

def test_detects_a_crash():
    df = _with_crash()
    found = detectors.detect(df, "TEST")
    planted_ts = int(df["ts"].iloc[300])
    at_bar = [e for e in found if e.ts == planted_ts]
    assert at_bar, "심어둔 급락 봉에서 아무 사건도 안 나왔다"
    assert "급락" in at_bar[0].title
    # 한 봉이 크게 빠지면 거래량·변동성도 같이 튄다. 합쳐진 사건이어야 한다.
    assert at_bar[0].severity >= 0.9


def test_same_bar_detections_are_merged():
    """급락·거래량 폭발·변동성 급등이 한 봉에서 같이 나면 사건 하나다."""
    df = _with_crash()
    found = detectors.detect(df, "TEST")
    stamps = [e.ts for e in found]
    assert len(stamps) == len(set(stamps)), "같은 봉에 사건이 두 개 이상 남았다"


def test_detection_uses_only_the_past():
    """앞부분을 잘라 내도 이미 확정된 구간의 판정은 그대로여야 한다.

    전체 구간의 분포로 자르면 나중 데이터가 옛날 판정을 바꾼다 — 그건 미래를 보는 것이다.
    """
    df = _with_crash(bars=500, at=300)
    early = detectors.detect(df.iloc[:380].reset_index(drop=True), "TEST")
    late = detectors.detect(df, "TEST")
    early_ts = {e.ts for e in early}
    late_ts = {e.ts for e in late if e.ts <= int(df["ts"].iloc[379])}
    assert early_ts == late_ts


def test_short_history_detects_nothing():
    assert detectors.detect(make_candles(count=20), "TEST") == []


# --- 사전과 반복 일정 --------------------------------------------------------

def test_seed_events_load():
    events = catalog.seed()
    assert len(events) > 10
    assert all(e.source == "seed" for e in events)
    assert [e.ts for e in events] == sorted(e.ts for e in events)


def test_recurring_finds_month_and_quarter_ends():
    start = int(pd.Timestamp("2026-01-01", tz="UTC").timestamp() * 1000)
    end = int(pd.Timestamp("2026-12-31", tz="UTC").timestamp() * 1000)
    found = catalog.recurring(start, end)
    titles = [e.title for e in found]
    assert titles.count("월말") == 12
    assert titles.count("분기말") == 4
    assert all(e.scheduled for e in found)


def test_third_friday_is_actually_a_friday():
    for month in range(1, 13):
        assert catalog._third_friday(2026, month).weekday() == 4


# --- 중복 접기 --------------------------------------------------------------

def test_event_set_folds_duplicates():
    """같은 사건이 두 소스로 들어와도 한 건이어야 한다. 두 건이면 이벤트 스터디가
    같은 날을 두 번 센다."""
    ts = 1_700_000_000_000
    a = Event(ts=ts, kind="macro", title="금리 인상", source="fred")
    b = Event(ts=ts + 60_000, kind="macro", title="금리 인상", source="gdelt")
    events = EventSet().add(a, b)
    assert len(events) == 1


def test_event_scope_filtering():
    events = EventSet().add(
        Event(ts=1, kind="macro", title="전체", source="seed", scope="global"),
        Event(ts=2, kind="crypto", title="코인", source="seed", scope="market:crypto"),
        Event(ts=3, kind="company", title="애플", source="seed", scope="symbol:AAPL"),
    )
    assert len(events.for_symbol("BTCUSDT", "crypto")) == 2
    assert len(events.for_symbol("AAPL", "us")) == 2
    assert len(events.for_symbol("MSFT", "us")) == 1


# --- 이벤트 스터디 -----------------------------------------------------------

def test_event_study_finds_a_planted_drop():
    """사건 뒤에 일부러 떨어뜨려 두고, CAR 이 음수로 나오는지 본다."""
    df = make_candles(count=900, seed=11).copy()
    rows = df.to_dict("records")
    marks = [300, 450, 600, 750]
    for at in marks:
        # 사건 뒤 열 봉 동안 매 봉 1% 씩 빠지고, 그 수준이 유지된다.
        # 열 봉 뒤에 원래 값으로 되돌리면 창 마지막에 +10% 반등이 생겨 CAR 이 상쇄된다.
        for i in range(at, len(rows)):
            steps = min(i - at + 1, 10)
            for key in ("open", "high", "low", "close"):
                rows[i][key] *= 0.99 ** steps
    planted = to_frame(rows)

    events = [Event(ts=int(planted["ts"].iloc[at]), kind="macro",
                    title=f"심어둔 사건 {at}", source="seed") for at in marks]
    result = study.aggregate(planted, events, before=3, after=10)
    assert result["available"]
    assert result["count"] == len(marks)
    assert result["finalCarPct"] < -5.0, result["finalCarPct"]
    assert result["hitRate"] == 0.0


def test_event_study_needs_enough_events():
    df = make_candles(count=400)
    one = [Event(ts=int(df["ts"].iloc[300]), kind="macro", title="한 건", source="seed")]
    result = study.aggregate(df, one, before=3, after=10)
    assert result["available"] is False
    assert "최소" in result["reason"]


def test_event_study_flags_overlapping_windows():
    """사건 창이 겹치면 t 값이 부풀려진다. 그 사실을 표시해야 한다."""
    df = make_candles(count=600, seed=13)
    close = [Event(ts=int(df["ts"].iloc[i]), kind="macro", title=f"e{i}", source="seed")
             for i in (300, 303, 306, 309)]
    result = study.aggregate(df, close, before=3, after=20)
    assert result["available"]
    assert result["overlapping"] is True


def test_event_study_skips_events_without_enough_history():
    """맨 앞의 사건은 추정창을 못 만든다. 조용히 0으로 세면 안 되고 빼야 한다."""
    df = make_candles(count=400)
    early = [Event(ts=int(df["ts"].iloc[i]), kind="macro", title=f"e{i}", source="seed")
             for i in (5, 10, 15)]
    result = study.aggregate(df, early, before=3, after=10)
    assert result["available"] is False


def test_event_study_groups_by_kind():
    df = make_candles(count=800, seed=17)
    events = []
    for i, at in enumerate(range(250, 700, 40)):
        events.append(Event(ts=int(df["ts"].iloc[at]),
                            kind="macro" if i % 2 else "chart",
                            title=f"e{at}", source="seed"))
    groups = study.by_group(df, events, "kind", before=2, after=10)
    labels = {g["label"] for g in groups}
    assert labels == {"macro", "chart"}


# --- 구간 밖 사건 차단 --------------------------------------------------------

async def test_collect_drops_out_of_range_events(monkeypatch):
    """소스가 범위를 안 지켜도 수집기가 잘라야 한다.

    실제로 FRED 가 월별 시리즈에서 날짜 파라미터를 무시하고 1947년부터 전부 돌려준
    적이 있다. 그때 사건 목록이 717건으로 부풀었고 화면의 숫자가 거짓말이 됐다.
    """
    from marketlens.events import collector

    df = make_candles(count=300)
    inside = int(df["ts"].iloc[150])
    outside = int(df["ts"].iloc[0]) - 10 * 365 * 86_400_000  # 10년 전

    monkeypatch.setattr(collector.catalog, "builtin", lambda a, b: [
        Event(ts=inside, kind="macro", title="구간 안", source="seed"),
        Event(ts=outside, kind="macro", title="구간 밖", source="seed"),
    ])
    found, status = await collector.collect(df, "TEST", "crypto", sources=("builtin",))
    titles = [e.title for e in found.events]
    assert titles == ["구간 안"]
    assert status["builtin"]["count"] == 1
