"""알림 라우트.

읽기는 가볍고, 쓰기는 규칙 하나를 만들거나 끄는 정도다. **주문을 내는 엔드포인트는
없다** — 이 앱은 알림까지고 매매는 사람이 한다.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..alerts import followup, push, store, watch
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


@router.get("/log")
def log(days: int = 30, symbol: str | None = None, kind: str | None = None,
        archived: bool = True, limit: int = 2000) -> dict:
    """알림 기록. **알림함과 따로 둔다.**

    알림함은 "지금 봐야 할 것"이고 기록은 "그때 뭐가 왔었나"다. 한 목록에 두면
    읽고 보관하는 순간 눈앞에서 사라지는데, 정작 뒤에 세어 볼 것은 그것들이다.
    그래서 여기는 **보관한 것도 기본으로 보인다.**

    `days=0` 은 전체다.
    """
    if kind is not None and kind not in store.KINDS:
        raise HTTPException(400, f"모르는 종류: {kind} (가능: {', '.join(store.KINDS)})")
    since = None
    if days > 0:
        since = (datetime.now(timezone.utc) - timedelta(days=days)) \
            .isoformat(timespec="seconds")

    entries = store.log(since=since, symbol=symbol, kind=kind,
                        include_archived=archived, limit=limit)
    by_symbol = Counter(e["symbol"] for e in entries if e.get("symbol"))
    return {
        "entries": entries,
        "summary": {
            "total": len(entries),
            # 필터가 뭘 숨겼는지 화면이 말할 수 있어야 한다. 안 그러면 "기록이
            # 세 건뿐" 으로 읽힌다.
            "stored": store.count(),
            "unread": sum(1 for e in entries if not e.get("read")),
            "archived": sum(1 for e in entries if e.get("archived")),
            "kinds": dict(Counter(e["kind"] for e in entries if e.get("kind"))),
            "symbols": [{"symbol": s, "label": watch.label(s), "count": n}
                        for s, n in by_symbol.most_common()],
            # 목록이 최근 것부터라 처음과 끝이 뒤집혀 있다.
            "first": entries[-1]["at"] if entries else None,
            "last": entries[0]["at"] if entries else None,
        },
    }


class OutcomeAsk(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)


@router.post("/log/outcome")
async def outcome(body: OutcomeAsk) -> dict:
    """기록들의 뒷값. 화면이 보고 있는 것만 물어본다.

    목록을 열 때마다 부르지 않는 건, 시세 조회가 프로바이더를 타서 느리고 실패도
    하기 때문이다. **뒷값이 안 와도 기록 자체는 떠 있어야 한다.**
    """
    want = set(body.ids)
    entries = [e for e in store.log() if e.get("id") in want]
    if not entries:
        return {"outcomes": {}, "failed": {}, "horizons": list(followup.HORIZONS)}
    return await followup.measure(entries)


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
