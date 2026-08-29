"""알림 라우트.

읽기는 가볍고, 쓰기는 규칙 하나를 만들거나 끄는 정도다. **주문을 내는 엔드포인트는
없다** — 이 앱은 알림까지고 매매는 사람이 한다.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..alerts import push, store, watch
from ..alerts.store import Rule
from . import recommend as recommend_layer

router = APIRouter(prefix="/api/alerts")


class NewRule(BaseModel):
    provider: str
    symbol: str
    kind: str
    price: float = Field(gt=0)
    note: str = ""


class Patch(BaseModel):
    active: bool | None = None
    price: float | None = Field(default=None, gt=0)
    note: str | None = None
    # 이미 나간 규칙을 다시 켠다. `fired_at` 을 지워야 감시가 다시 본다.
    rearm: bool | None = None


@router.get("")
def listing() -> dict:
    """규칙과 최근 알림. 화면 하나가 이걸로 다 그려진다."""
    return {
        "rules": [watch.as_dict(r) for r in store.rules()],
        "fired": store.fired(200),
        "quiet": watch.quiet_now(),
        "push": {"available": push.available(), "publicKey": push.public_key()},
    }


@router.post("")
def create(body: NewRule) -> dict:
    if body.kind not in store.KINDS:
        raise HTTPException(400, f"모르는 종류: {body.kind} (가능: {', '.join(store.KINDS)})")
    rule = store.add(Rule(provider=body.provider, symbol=body.symbol,
                          kind=body.kind, price=body.price, note=body.note))
    return watch.as_dict(rule)


@router.patch("/{rule_id}")
def change(rule_id: str, body: Patch) -> dict:
    changes: dict = {}
    if body.active is not None:
        changes["active"] = body.active
    if body.price is not None:
        changes["price"] = body.price
    if body.note is not None:
        changes["note"] = body.note
    if body.rearm:
        # 다시 켤 때는 **활성도 같이 켠다.** 껐다 되살리는 게 한 동작이어야 한다.
        changes["fired_at"] = None
        changes["active"] = True
    found = store.update(rule_id, **changes)
    if found is None:
        raise HTTPException(404, "그런 규칙이 없다")
    return watch.as_dict(found)


@router.delete("/{rule_id}")
def drop(rule_id: str) -> dict:
    if not store.remove(rule_id):
        raise HTTPException(404, "그런 규칙이 없다")
    return {"ok": True}


@router.post("/fired/{entry_id}/read")
def read(entry_id: str) -> dict:
    return {"ok": store.mark(entry_id, read=True)}


@router.post("/fired/{entry_id}/archive")
def archive(entry_id: str) -> dict:
    """화면에서 치운다. **기록은 지우지 않는다** — 알림이 맞았는지 나중에 재야 한다."""
    return {"ok": store.mark(entry_id, archived=True, read=True)}


@router.post("/from-recommendation")
def from_recommendation(provider: str, days: int = 1) -> dict:
    """오늘 추천을 규칙으로 만든다. 종목당 살 값·팔 값 두 개."""
    body = recommend_layer.today(provider, days)
    if not body.get("available"):
        raise HTTPException(400, body.get("reason", "추천이 없다"))
    made = watch.from_recommendation(provider, days, body)
    return {"made": [watch.as_dict(r) for r in made],
            "date": body.get("date"),
            # 화면이 성적을 같이 보여줄 수 있게 넘긴다. 추천 실적은 아직 0건이다.
            "record": body.get("record"), "measured": body.get("measured")}


class Subscription(BaseModel):
    endpoint: str
    keys: dict


@router.post("/subscribe")
def subscribe(body: Subscription) -> dict:
    store.subscribe({"endpoint": body.endpoint, "keys": body.keys})
    return {"ok": True, "count": len(store.subscriptions())}


@router.post("/unsubscribe")
def unsubscribe(body: Subscription) -> dict:
    store.unsubscribe(body.endpoint)
    return {"ok": True, "count": len(store.subscriptions())}


@router.post("/test")
async def test() -> dict:
    """폰에서 실제로 뜨는지 확인하는 용도. **앱을 닫고** 눌러 봐야 의미가 있다."""
    entry = {"id": f"test-{store.now()}", "title": "market-lens 알림 시험",
             "body": "이게 보이면 푸시가 살아 있다", "at": store.now(),
             "read": False, "archived": False}
    store.record_fired(entry)
    sent = await push.send(entry)
    return {"sent": sent, "subscriptions": len(store.subscriptions()),
            "push": push.available()}
