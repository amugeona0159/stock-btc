"""알림 기록 — 남는가, 걸러지는가, 뒤가 붙는가.

기록이 지켜야 하는 건 하나다: **알림함에서 사라진 것이 여기에는 남아 있어야 한다.**
읽고 보관한 알림이 여기서도 없어지면 "그때 알림이 왔었나"를 영영 못 센다.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from marketlens.alerts import followup, store

DAY_MS = 86_400_000


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "FOLDER", tmp_path)
    monkeypatch.setattr(store, "RULES", tmp_path / "rules.json")
    monkeypatch.setattr(store, "FIRED", tmp_path / "fired.jsonl")
    monkeypatch.setattr(store, "SUBS", tmp_path / "subscriptions.json")


def _at(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)) \
        .isoformat(timespec="seconds")


def _entry(entry_id: str, *, days_ago: float = 0.0, symbol: str = "KRW-SOL",
           kind: str = "buy_below", price: float = 100.0, **rest) -> dict:
    row = {"id": entry_id, "provider": "upbit", "symbol": symbol, "kind": kind,
           "price": price, "setPrice": price, "at": _at(days_ago),
           "title": "t", "body": "b", "read": False, "archived": False}
    row.update(rest)
    return row


# --------------------------------------------------------------- 기록 읽기

def test_log_keeps_what_the_inbox_hides():
    """보관은 알림함에서 치우는 표시지 기록에서 지우는 게 아니다."""
    store.record_fired(_entry("a"))
    store.record_fired(_entry("b"))
    store.mark("a", archived=True, read=True)

    assert [r["id"] for r in store.log()] == ["b", "a"]
    assert [r["id"] for r in store.log(include_archived=False)] == ["b"]


def test_log_has_no_ceiling_by_default():
    """`fired()` 는 200 에서 끊는다. 기록까지 끊으면 옛 알림이 조용히 사라진다."""
    for i in range(250):
        store.record_fired(_entry(f"e{i}"))

    assert len(store.fired()) == 200
    assert len(store.log()) == 250
    assert len(store.log(limit=10)) == 10
    assert store.count() == 250


def test_log_filters_do_not_overlap():
    store.record_fired(_entry("old", days_ago=40))
    store.record_fired(_entry("btc", symbol="BTCUSDT"))
    store.record_fired(_entry("stop", kind="stop_below"))

    since = _at(30)
    assert [r["id"] for r in store.log(since=since)] == ["stop", "btc"]
    assert [r["id"] for r in store.log(symbol="BTCUSDT")] == ["btc"]
    assert [r["id"] for r in store.log(kind="stop_below")] == ["stop"]


def test_newest_is_on_top():
    store.record_fired(_entry("first", days_ago=2))
    store.record_fired(_entry("second", days_ago=1))
    assert [r["id"] for r in store.log()] == ["second", "first"]


# --------------------------------------------------------------- 뒷값

def _frame(base_ms: int, closes: list[float], *, last_open: bool = False):
    """일봉. `last_open` 이면 마지막 봉이 아직 안 닫혔다."""
    closed = [True] * len(closes)
    if last_open:
        closed[-1] = False
    return pd.DataFrame({
        "ts": [base_ms + i * DAY_MS for i in range(len(closes))],
        "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [1.0] * len(closes), "closed": closed,
    })


def _bars_around(entry: dict, closes: list[float], *, last_open: bool = False):
    """알림이 든 봉이 index 1 이 되게 세운다.

    기록의 `at` 은 초 단위로 잘려 저장된다. 봉 시각을 따로 계산해 두면 그 절삭
    때문에 경계에서 한 봉씩 밀린다 — 그래서 기록 자신의 시각에서 잡는다.
    """
    at_ms = int(datetime.fromisoformat(entry["at"]).timestamp() * 1000)
    return _frame(at_ms - DAY_MS - 60_000, closes, last_open=last_open)


class _Fake:
    def __init__(self, frame, error: Exception | None = None):
        self.frame = frame
        self.error = error
        self.calls = 0

    async def history(self, symbol, timeframe, limit=500):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.frame


def _install(monkeypatch, table: dict[str, _Fake]):
    monkeypatch.setattr(followup, "get_provider", lambda key: table[key])


def test_change_is_measured_from_the_touched_price(monkeypatch):
    """기준은 설정값이 아니라 **닿은 값**이다. 알림을 받은 사람이 본 숫자가 그쪽이다."""
    entry = _entry("x", days_ago=9, price=100.0, setPrice=95.0)
    # 알림이 든 봉 = index 1. 그 뒤로 +1 · +3 · +7 이 있다.
    _install(monkeypatch, {"upbit": _Fake(_bars_around(
        entry, [90.0, 100.0, 110.0, 100.0, 120.0, 100.0, 100.0, 100.0, 130.0, 140.0]))})

    got = asyncio.run(followup.measure([entry]))
    outcome = got["outcomes"]["x"]

    assert outcome["base"] == 100.0
    assert outcome["after"]["1"]["price"] == 110.0
    assert outcome["after"]["1"]["changePct"] == pytest.approx(10.0)
    assert outcome["after"]["3"]["price"] == 120.0
    assert outcome["after"]["7"]["price"] == 130.0
    assert outcome["latest"]["price"] == 140.0
    assert not got["failed"]


def test_the_unfinished_bar_is_not_used(monkeypatch):
    """마지막 봉은 종가가 아직 안 굳었다. 끼우면 제일 최근 기록만 조용히 틀린다."""
    entry = _entry("x", days_ago=2, price=100.0)
    _install(monkeypatch, {"upbit": _Fake(
        _bars_around(entry, [90.0, 100.0, 110.0, 999.0], last_open=True))})

    got = asyncio.run(followup.measure([entry]))
    assert got["outcomes"]["x"]["latest"]["price"] == 110.0


def test_one_symbol_is_asked_once(monkeypatch):
    """같은 종목에 걸린 기록이 스무 건이면 스무 번 부르게 된다 — 토스는 429 를 준다."""
    rows = [_entry(f"e{i}", days_ago=5, price=100.0) for i in range(8)]
    fake = _Fake(_bars_around(rows[0], [90.0, 100.0, 110.0, 120.0, 130.0, 140.0]))
    _install(monkeypatch, {"upbit": fake})

    got = asyncio.run(followup.measure(rows))

    assert fake.calls == 1
    assert len(got["outcomes"]) == 8


def test_a_dead_provider_does_not_take_the_rest_down(monkeypatch):
    """국내주식은 IP 를 안 걸어 두면 못 붙는다. 그때 코인 기록까지 비면 안 된다."""
    rows = [
        _entry("coin", days_ago=3, price=100.0),
        _entry("stock", days_ago=3, price=100.0, provider="toss_kr", symbol="005930"),
    ]
    alive = _Fake(_bars_around(rows[0], [90.0, 100.0, 110.0, 120.0]))
    dead = _Fake(None, error=RuntimeError("허용 목록에 없는 IP"))
    _install(monkeypatch, {"upbit": alive, "toss_kr": dead})

    got = asyncio.run(followup.measure(rows))

    assert "coin" in got["outcomes"]
    assert "stock" not in got["outcomes"]
    assert "toss_kr:005930" in got["failed"]


def test_entries_without_a_price_are_skipped(monkeypatch):
    """시험 알림과 밤사이 묶음에는 종목도 가격도 없다. 그것들로 시세를 부르면 안 된다."""
    fake = _Fake(None, error=AssertionError("불리면 안 된다"))
    _install(monkeypatch, {"upbit": fake})

    rows = [
        {"id": "test-1", "title": "시험", "body": "", "at": _at(1),
         "read": False, "archived": False},
        {"id": "batch-1", "title": "밤사이 알림 3건", "body": "", "at": _at(1),
         "read": False, "archived": False, "batch": True},
    ]
    got = asyncio.run(followup.measure(rows))

    assert fake.calls == 0
    assert got["outcomes"] == {}
    assert got["failed"] == {}
