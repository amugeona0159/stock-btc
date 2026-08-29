"""포지션 — 계획·알림·부분 익절·트레일링·성적표.

이 파일이 지키는 건 셋이다.

1. **장부가 실제 잔고와 갈라지지 않는다.** 닿았다고 판 것으로 치지 않는다.
2. **손절선은 내려가지 않는다.** 올린 뒤에 옛 규칙이 남아 두 번 울리지 않는다.
3. **통화를 더하지 않는다.** 원화 판과 달러 판을 한 숫자로 만들지 않는다.
"""
from __future__ import annotations

import pytest

from marketlens.alerts import store as alerts
from marketlens.positions import manage, plan, record
from marketlens.positions.store import CLOSED, OPEN, Position, get, save


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """알림 폴더 하나만 옮기면 포지션도 같이 격리된다 — 같은 폴더에 살기 때문."""
    monkeypatch.setattr(alerts, "FOLDER", tmp_path)
    monkeypatch.setattr(alerts, "RULES", tmp_path / "rules.json")
    monkeypatch.setattr(alerts, "FIRED", tmp_path / "fired.jsonl")
    monkeypatch.setattr(alerts, "SUBS", tmp_path / "subscriptions.json")


def _open(entry=100.0, shares=10.0, band=(-5.0, 12.0), expected=6.0, **rest):
    return manage.open_position(
        provider="upbit", symbol="KRW-SOL", entry=entry, shares=shares,
        currency="KRW", band=list(band) if band else None, expected=expected,
        days=1, source="recommend", **rest)


# ------------------------------------------------------------------ 계획

def test_the_plan_comes_from_the_band():
    """고정 비율이 아니라 80% 밴드에서 나온다. 종목마다 폭이 다르기 때문이다."""
    made = plan.from_band(100.0, [-5.0, 12.0], 6.0)
    assert made["stop"] == pytest.approx(95.0)
    assert [t["price"] for t in made["targets"]] == pytest.approx([106.0, 112.0])
    assert [t["portion"] for t in made["targets"]] == [0.5, 0.5]
    assert made["riskPct"] == pytest.approx(5.0, abs=0.1)


def test_no_plan_when_the_band_says_it_only_goes_up():
    """아래끝이 0 이상이면 손절가를 놓을 데가 없다. 지어내지 않는다."""
    assert plan.from_band(100.0, [1.0, 12.0], 6.0) is None
    assert plan.from_band(100.0, None, 6.0) is None


def test_a_target_that_the_fee_would_eat_is_dropped():
    """기대값이 진입가와 붙어 있으면 목표가 아니다."""
    made = plan.from_band(100.0, [-5.0, 12.0], 0.05)
    assert [t["price"] for t in made["targets"]] == pytest.approx([112.0])
    assert made["targets"][0]["portion"] == 1.0        # 하나뿐이면 전부를 가진다


def test_atr_is_the_fallback():
    made = plan.from_atr(100.0, 0.04)
    assert made["source"] == "atr"
    assert made["stop"] == pytest.approx(94.0)
    assert [t["price"] for t in made["targets"]] == pytest.approx([106.0, 112.0])


def test_a_position_opens_even_without_a_plan():
    """산 것은 이미 샀다. 계획을 못 냈다고 장부를 안 만들면 값을 볼 데가 없다."""
    position, why = manage.open_position(
        provider="upbit", symbol="KRW-SOL", entry=100.0, shares=1.0,
        currency="KRW", band=None, atr_pct=None)
    assert position.status == OPEN and position.stop == 0.0
    assert "못 냈다" in why


# ------------------------------------------------------------------ 알림 연결

def test_opening_arms_a_stop_and_the_targets():
    position, _ = _open()
    rules = alerts.rules()
    assert {r.kind for r in rules} == {"stop_below", "target_above"}
    assert all(r.position_id == position.id for r in rules)
    assert len(rules) == 3                     # 손절 하나 + 목표 둘


def test_a_hit_is_recorded_but_nothing_is_sold():
    """**닿았다고 판 것으로 치지 않는다.** 실제로 팔았는지는 사람만 안다."""
    position, _ = _open()
    first = next(r for r in alerts.rules()
                 if r.kind == "target_above" and r.price == pytest.approx(106.0))

    manage.on_fired(first, {"price": 106.5})

    after = get(position.id)
    assert after.shares_left == 10.0 and after.realized == 0.0
    assert after.targets[0]["hitAt"] is not None
    assert len(after.pending()) == 1


# ------------------------------------------------------------------ 두 갈래

def test_selling_half_moves_the_stop_to_break_even():
    """절반을 덜면 남은 절반은 잃지 않는 자리로 간다."""
    position, _ = _open()
    first = next(r for r in alerts.rules() if r.price == pytest.approx(106.0))
    manage.on_fired(first, {"price": 106.0})

    after = manage.sold(get(position.id), price=106.0, shares=5.0)

    assert after.shares_left == 5.0
    assert after.realized == pytest.approx(30.0)       # (106-100) × 5
    assert after.stop == pytest.approx(100.0)          # 본전
    assert after.status == OPEN


def test_holding_instead_of_selling_raises_the_stop():
    """안 팔았다고 하면 트레일링이다 — 값은 그대로, 손절선만 올라간다."""
    position, _ = _open()
    first = next(r for r in alerts.rules() if r.price == pytest.approx(106.0))
    manage.on_fired(first, {"price": 106.0})

    after = manage.held(get(position.id))

    assert after.shares_left == 10.0 and after.realized == 0.0
    assert after.stop == pytest.approx(100.0)
    assert not after.pending()


def test_the_old_stop_rule_is_replaced_not_stacked():
    """옛 손절 규칙이 남으면 이미 올려 둔 자리 아래에서 또 울린다."""
    position, _ = _open()
    old_stop = next(r for r in alerts.rules() if r.kind == "stop_below")
    first = next(r for r in alerts.rules() if r.price == pytest.approx(106.0))
    manage.on_fired(first, {"price": 106.0})

    after = manage.held(get(position.id))

    stops = [r for r in alerts.rules() if r.kind == "stop_below"]
    assert len(stops) == 1
    assert stops[0].id != old_stop.id
    assert stops[0].price == pytest.approx(after.stop)


def test_the_second_target_trails_to_the_first():
    """2차에 닿고 안 팔았으면 손절선은 1차까지 따라 올라간다."""
    position, _ = _open()
    for price in (106.0, 112.0):
        rule = next(r for r in alerts.rules()
                    if r.kind == "target_above" and r.price == pytest.approx(price))
        manage.on_fired(rule, {"price": price})
        manage.held(get(position.id))

    assert get(position.id).stop == pytest.approx(106.0)


def test_selling_everything_closes_and_takes_the_alerts_down():
    position, _ = _open()
    after = manage.sold(get(position.id), price=112.0, shares=10.0)

    assert after.status == CLOSED and after.close_reason == "target"
    assert after.realized == pytest.approx(120.0)
    assert alerts.rules() == []                       # 걸어 둔 알림을 거둔다


def test_closing_takes_the_alerts_down_too():
    position, _ = _open()
    manage.close(get(position.id), reason="stop")
    assert alerts.rules() == []


# ------------------------------------------------------------------ 성적표

def test_currencies_are_not_added_together():
    """원화 판과 달러 판을 한 숫자로 더하면 아무 뜻도 없는 값이 된다."""
    won = Position(provider="upbit", symbol="KRW-SOL", entry=100.0, shares=10.0,
                   currency="KRW", realized=200.0, status=CLOSED,
                   close_reason="target")
    dollar = Position(provider="binance", symbol="BTCUSDT", entry=50.0, shares=2.0,
                      currency="USD", realized=-10.0, status=CLOSED,
                      close_reason="stop")
    save(won)
    save(dollar)

    got = record.summary([won, dollar])

    assert got["n"] == 2 and got["wins"] == 1 and got["winRate"] == 0.5
    assert got["byCurrency"]["KRW"]["realized"] == 200.0
    assert got["byCurrency"]["USD"]["realized"] == -10.0
    assert got["reasons"] == {"target": 1, "stop": 1}


def test_profit_pct_uses_the_whole_stake():
    """남은 주수로 나누면 부분 익절한 판의 수익률이 두 배로 부푼다."""
    position, _ = _open()
    after = manage.sold(get(position.id), price=106.0, shares=5.0)
    got = record.one(after)
    # 30 / (100 × 10) = 3%. 남은 5주로 나눴다면 6% 가 됐을 것이다.
    assert got["profitPct"] == pytest.approx(3.0)


def test_a_fully_sold_position_stays_at_zero_shares():
    """다 판 판을 다시 읽었을 때 **판 주식이 되살아나면 안 된다.**

    남은 주수 0 을 "안 적혔다"로 읽으면 원래 수량으로 되돌아가고, 닫힌 판이 아직
    들고 있는 것처럼 보인다. 실제로 그렇게 만들었다가 잡았다.
    """
    position, _ = _open(shares=10.0)
    manage.sold(get(position.id), price=112.0, shares=10.0)

    again = get(position.id)
    assert again.status == CLOSED
    assert again.shares_left == 0.0
    assert again.cost == 0.0
