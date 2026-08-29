"""알림 — 규칙 저장 · 감시 · 문구.

이 파일이 지키는 건 **알림 앱을 못 쓰게 만드는 두 가지**다: 안 오는 것과 쏟아지는 것.
"""
from __future__ import annotations

import asyncio

import pytest

from marketlens.alerts import store, watch
from marketlens.alerts.store import Rule


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """테스트가 진짜 알림 파일을 건드리지 않게 한다."""
    monkeypatch.setattr(store, "FOLDER", tmp_path)
    monkeypatch.setattr(store, "RULES", tmp_path / "rules.json")
    monkeypatch.setattr(store, "FIRED", tmp_path / "fired.jsonl")
    monkeypatch.setattr(store, "SUBS", tmp_path / "subscriptions.json")


# --------------------------------------------------------------- 규칙 저장

def test_rule_direction_matters():
    """같은 가격이라도 **어느 쪽에서 오느냐**가 다르다."""
    below = Rule(provider="upbit", symbol="KRW-SOL", kind="buy_below", price=100.0)
    above = Rule(provider="upbit", symbol="KRW-SOL", kind="sell_above", price=100.0)
    assert below.hits(99.0) and not below.hits(101.0)
    assert above.hits(101.0) and not above.hits(99.0)
    assert below.hits(100.0) and above.hits(100.0)      # 정확히 닿은 것도 닿은 것이다


def test_same_rule_is_not_added_twice():
    """아침마다 추천이 규칙을 만든다. 이틀 연속 같은 추천이면 알림이 두 번 온다."""
    first = store.add(Rule(provider="upbit", symbol="KRW-SOL",
                           kind="buy_below", price=100.0))
    again = store.add(Rule(provider="upbit", symbol="KRW-SOL",
                           kind="buy_below", price=100.0))
    assert first.id == again.id
    assert len(store.rules()) == 1


def test_a_disabled_rule_does_not_block_a_new_one():
    """껐던 규칙과 같은 값을 다시 걸 수 있어야 한다."""
    old = store.add(Rule(provider="upbit", symbol="KRW-SOL",
                         kind="buy_below", price=100.0))
    store.update(old.id, active=False)
    fresh = store.add(Rule(provider="upbit", symbol="KRW-SOL",
                           kind="buy_below", price=100.0))
    assert fresh.id != old.id


def test_archiving_keeps_the_record():
    """화면에서 치워도 **기록은 남는다** — 알림이 맞았는지 나중에 재야 한다."""
    store.record_fired({"id": "x1", "title": "t", "body": "b",
                        "read": False, "archived": False})
    assert store.mark("x1", archived=True, read=True)
    rows = store.fired()
    assert len(rows) == 1 and rows[0]["archived"] and rows[0]["read"]


def test_subscription_is_not_duplicated():
    """폰에서 새로고침할 때마다 쌓이면 알림이 여러 번 온다."""
    for _ in range(3):
        store.subscribe({"endpoint": "https://push/1", "keys": {"a": "b"}})
    assert len(store.subscriptions()) == 1


# ------------------------------------------------------------------ 감시

def test_touch_uses_the_bar_extremes_not_the_close():
    """30초에 한 번 보는데 종가만 보면 **찍고 돌아온 값**을 놓친다."""
    rule = Rule(provider="upbit", symbol="KRW-SOL", kind="buy_below", price=100.0)
    assert watch.touched(rule, low=99.0, high=120.0) == 99.0     # 저가로 닿았다
    assert watch.touched(rule, low=101.0, high=120.0) is None

    up = Rule(provider="upbit", symbol="KRW-SOL", kind="sell_above", price=100.0)
    assert watch.touched(up, low=90.0, high=101.0) == 101.0      # 고가로 닿았다


@pytest.fixture
def daytime(monkeypatch):
    """벽시계를 낮으로 고정한다.

    아래 두 판이 보는 건 "한 번만 나가는가" 와 "하나가 죽어도 나머지가 나가는가"
    지 시각이 아니다. 그런데 한밤중(23~07 KST)에는 급하지 않은 알림을 모아 뒀다
    아침에 보내므로, 벽시계를 그대로 쓰면 **새벽에 돌린 사람만** 이 둘이 빨개진다.
    """
    monkeypatch.setattr(watch, "quiet_now", lambda *_args, **_kwargs: False)


def test_a_rule_fires_only_once(daytime):
    """**폭주 방지.** 경계에서 가격이 떨어도 한 번만 나가야 한다."""
    rule = store.add(Rule(provider="upbit", symbol="KRW-SOL",
                          kind="buy_below", price=100.0))
    sent = []
    watcher = watch.Watcher(lambda entry: _record(sent, entry))

    async def price(_rule):
        return (99.0, 101.0)                      # 계속 닿아 있는 상태

    async def run():
        import marketlens.alerts.watch as module
        module.latest_price = price
        for _ in range(5):
            await watcher.tick()

    asyncio.run(run())
    assert len(sent) == 1, f"{len(sent)}번 나갔다 — 폭주 방지가 깨졌다"
    assert store.rules()[0].fired_at is not None
    assert rule.id == store.rules()[0].id


def test_a_broken_symbol_does_not_stop_the_others(daytime):
    """프로바이더 하나가 죽었다고 **나머지 알림까지 죽으면 안 된다.**"""
    store.add(Rule(provider="nosuch", symbol="BAD", kind="buy_below", price=100.0))
    store.add(Rule(provider="upbit", symbol="KRW-SOL", kind="buy_below", price=100.0))
    sent = []
    watcher = watch.Watcher(lambda entry: _record(sent, entry))

    async def price(rule):
        if rule.symbol == "BAD":
            raise RuntimeError("시세 실패")
        return (99.0, 101.0)

    async def run():
        import marketlens.alerts.watch as module
        module.latest_price = price
        await watcher.tick()

    asyncio.run(run())
    assert len(sent) == 1 and sent[0]["symbol"] == "KRW-SOL"


def test_quiet_hours_hold_normal_alerts_but_not_stops():
    """한밤중에는 모아 두되, **손절은 자고 있어도 알아야 한다.**"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    night = datetime(2026, 8, 30, 2, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    day = datetime(2026, 8, 30, 14, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    assert watch.quiet_now(night) and not watch.quiet_now(day)


# ------------------------------------------------------------------ 문구

def test_message_never_tells_you_to_buy():
    """방향 적중 55% 짜리 모델로 **명령형을 쓰지 않는다.**"""
    rule = Rule(provider="upbit", symbol="KRW-SOL", kind="buy_below", price=100.0,
                band=[-3.4, 5.8], days=3)
    got = watch.message(rule, 99.0)
    text = got["title"] + got["body"]
    for banned in ("사세요", "매수하세요", "파세요", "매도하세요"):
        assert banned not in text, f"명령형이 들어갔다: {banned}"
    assert "닿았다" in got["title"]


def test_message_states_the_band_not_a_clock_time():
    """모델이 아는 시간은 **1·2·3일 지평**뿐이다. '화요일 오후' 는 못 만든다."""
    rule = Rule(provider="upbit", symbol="KRW-SOL", kind="target_above", price=100.0,
                band=[-3.4, 5.8], days=3)
    body = watch.message(rule, 101.0)["body"]
    assert "3일 안에" in body and "80%" in body


def test_message_uses_korean_names():
    rule = Rule(provider="upbit", symbol="KRW-SOL", kind="buy_below", price=100.0)
    assert "솔라나" in watch.message(rule, 99.0)["title"]


def _record(bucket, entry):
    async def done():
        bucket.append(entry)
    return done()
