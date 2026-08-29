"""포지션을 열고, 알림에 반응하고, 닫는다.

## 알림과 포지션이 맞물리는 자리

포지션을 열면 손절 규칙 하나와 목표 규칙들이 같이 걸린다(`rule.position_id`). 감시
루프가 그 규칙을 울리면 `on_fired` 가 포지션에 "닿았다"를 적는다. **거기서 멈춘다** —
실제로 팔았는지는 사람만 안다.

    닿음 → (사람) 팔았다   → 그만큼 덜어내고 실현 손익에 더한다
         → (사람) 안 팔았다 → 손절가를 올려 다시 건다 (트레일링)

이 두 갈래가 부분 익절과 트레일링 스탑의 전부다. 자동으로 체결로 치면 장부가 실제
잔고와 갈라지고, 그 순간 손익은 아무 뜻도 없는 숫자가 된다.

## 규칙은 갈아 끼운다, 쌓지 않는다

손절가를 올릴 때 옛 규칙을 지우고 새로 건다. 안 지우면 옛 손절가에서도 알림이 울려,
이미 올려 둔 자리 아래에서 두 번 울린다.
"""
from __future__ import annotations

from ..alerts import store as alerts
from ..alerts.store import Rule
from ..screen import names
from . import plan
from .store import CLOSED, OPEN, Position, get, note_event, save

KINDS = {"stop": "stop_below", "target": "target_above"}


def _rule_for(position: Position, kind: str, price: float,
              label: str) -> Rule:
    note = f"{names.label(position.symbol)} · {label}"
    return alerts.add(Rule(
        provider=position.provider, symbol=position.symbol,
        kind=KINDS[kind], price=price, note=note,
        source="position", position_id=position.id,
        band=position.band, days=position.days,
    ))


def _drop_rule(rule_id: str | None) -> None:
    if rule_id:
        alerts.remove(rule_id)


def arm(position: Position) -> Position:
    """계획대로 알림을 건다. 이미 걸린 것은 지우고 다시 건다."""
    _drop_rule(position.stop_rule_id)
    if position.status == OPEN and position.stop > 0:
        rule = _rule_for(position, "stop", position.stop, "손절선")
        position.stop_rule_id = rule.id
        position.stop_hit_at = None
        position.stop_settled_at = None
    else:
        position.stop_rule_id = None

    for target in position.targets:
        if target.get("settledAt") or position.status == CLOSED:
            _drop_rule(target.get("ruleId"))
            target["ruleId"] = None
            continue
        if not target.get("ruleId"):
            rule = _rule_for(position, "target", target["price"], target["label"])
            target["ruleId"] = rule.id
    return position


def open_position(*, provider: str, symbol: str, entry: float, shares: float,
                  currency: str, band: list[float] | None = None,
                  expected: float | None = None, atr_pct: float | None = None,
                  days: int | None = None, model: str | None = None,
                  source: str = "manual", note: str = "") -> tuple[Position, str]:
    """(포지션, 계획을 못 세운 이유). 계획이 없어도 포지션 자체는 연다.

    **계획이 없다고 장부를 안 만들면 안 된다.** 산 것은 이미 샀으니, 값이 얼마인지는
    보여줘야 한다. 못 만든 건 손절·목표뿐이고 그건 화면이 말해 준다.
    """
    made = plan.from_band(entry, band, expected) or plan.from_atr(entry, atr_pct)
    position = Position(
        provider=provider, symbol=symbol, entry=entry, shares=shares,
        currency=currency, band=band, days=days, model=model,
        source=source, note=note,
        stop=made["stop"] if made else 0.0,
        targets=made["targets"] if made else [],
    )
    why = ""
    if made:
        note_event(position, "open",
                   f"{made['source']} 로 손절 {made['stop']:,.4g} · "
                   f"목표 {len(made['targets'])}개")
        arm(position)
    else:
        why = ("밴드도 변동성도 없어 손절·목표를 못 냈다 — "
               "값을 직접 넣으면 그때 알림을 건다")
        note_event(position, "open", why)
    return save(position), why


def on_fired(rule: Rule, entry: dict) -> None:
    """감시 루프가 규칙을 울렸을 때. **여기서 팔지 않는다.**

    포지션에 "닿았다"만 적는다. 사람이 화면에서 팔았는지 정하면 그때 장부가 움직인다.
    """
    if not rule.position_id:
        return
    position = get(rule.position_id)
    if position is None or position.status == CLOSED:
        return

    price = entry.get("price")
    if rule.id == position.stop_rule_id:
        position.stop_hit_at = alerts.now()
        note_event(position, "stop-hit", f"손절선 {position.stop:,.4g} 에 닿았다",
                   price=price)
        save(position)
        return

    for target in position.targets:
        if target.get("ruleId") == rule.id and not target.get("hitAt"):
            target["hitAt"] = alerts.now()
            note_event(position, "target-hit",
                       f"{target['label']} {target['price']:,.4g} 에 닿았다",
                       price=price, target=target["price"])
            save(position)
            return


def sold(position: Position, *, price: float, shares: float,
         reason: str = "target") -> Position:
    """실제로 판 만큼 덜어낸다. 남은 게 없으면 닫힌다."""
    taken = min(shares, position.shares_left)
    if taken <= 0:
        return position
    gain = (price - position.entry) * taken
    position.shares_left = round(position.shares_left - taken, 10)
    position.realized = round(position.realized + gain, 6)
    note_event(position, "sell",
               f"{taken:,.6g}주를 {price:,.4g} 에 팔았다 ({gain:+,.0f})",
               price=price, shares=taken, gain=gain, reason=reason)

    # 닿은 목표 중 아직 안 정한 것을 이 매도로 정리한다.
    for target in position.targets:
        if target.get("hitAt") and not target.get("settledAt"):
            target["settledAt"] = alerts.now()
            target["filledShares"] = taken
            target["filledPrice"] = price
            break

    if position.shares_left <= 1e-9:
        return close(position, reason=reason)

    # 절반을 덜었으면 남은 절반은 **잃지 않는 자리**로 옮긴다.
    moved = plan.trail_to(position.entry, position.targets, price)
    if moved > position.stop:
        position.stop = round(moved, 8)
        note_event(position, "trail", f"손절선을 {position.stop:,.4g} 로 올렸다")
    arm(position)
    return save(position)


def held(position: Position) -> Position:
    """닿았는데 안 팔았다. 손절가를 올려 다시 건다.

    **이게 트레일링 스탑이다.** 안 판 이유는 더 갈 것 같아서일 텐데, 그렇다면 최소한
    닿았던 자리 아래로는 안 내려가게 해 둔다.
    """
    hit = [t for t in position.targets if t.get("hitAt") and not t.get("settledAt")]
    for target in hit:
        target["settledAt"] = alerts.now()
        target["filledShares"] = 0.0
    top = max([t["price"] for t in hit], default=position.entry)
    moved = plan.trail_to(position.entry, position.targets, top)
    if moved > position.stop:
        position.stop = round(moved, 8)
        note_event(position, "trail",
                   f"안 팔았다 — 손절선을 {position.stop:,.4g} 로 올렸다")
    else:
        note_event(position, "hold", "안 팔았다 — 손절선은 그대로")
    if position.stop_hit_at and not position.stop_settled_at:
        position.stop_settled_at = alerts.now()
    arm(position)
    return save(position)


def close(position: Position, *, reason: str = "manual") -> Position:
    position.status = CLOSED
    position.closed_at = alerts.now()
    position.close_reason = reason
    _drop_rule(position.stop_rule_id)
    position.stop_rule_id = None
    for target in position.targets:
        _drop_rule(target.get("ruleId"))
        target["ruleId"] = None
    note_event(position, "close", f"닫혔다 ({reason}) · 실현 {position.realized:+,.0f}")
    return save(position)


def retarget(position: Position, *, stop: float | None = None,
             targets: list[dict] | None = None) -> Position:
    """손절가·목표를 손으로 고친다. 규칙을 갈아 끼운다."""
    if stop is not None:
        position.stop = round(stop, 8)
        note_event(position, "retarget", f"손절선을 {position.stop:,.4g} 로 정했다")
    if targets is not None:
        for old in position.targets:
            _drop_rule(old.get("ruleId"))
        position.targets = [
            {"price": round(float(t["price"]), 8),
             "portion": float(t.get("portion") or 1.0),
             "label": str(t.get("label") or "직접"),
             "ruleId": None, "hitAt": None, "settledAt": None,
             "filledShares": 0.0, "filledPrice": None}
            for t in targets
        ]
        note_event(position, "retarget", f"목표를 {len(position.targets)}개로 정했다")
    arm(position)
    return save(position)
