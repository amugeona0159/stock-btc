"""포지션 라우트.

**주문을 내는 엔드포인트는 없다.** 여기 있는 건 장부다 — 사람이 산 것을 적고, 값이
닿으면 알려 주고, 팔았다고 하면 덜어낸다. 매매는 사람이 자기 증권사에서 한다.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..positions import advice, manage, quotes, record
from ..positions.store import CLOSED, OPEN, Position, all_positions, currency_of
from ..positions.store import get as get_position
from ..positions.store import remove as remove_position
from ..screen import names
from . import recommend as recommend_layer

router = APIRouter(prefix="/api/positions")


class NewPosition(BaseModel):
    provider: str
    symbol: str
    entry: float = Field(gt=0)
    shares: float = Field(gt=0)
    note: str = ""
    # 추천에서 열면 그날 근거가 같이 온다. 손으로 열면 없고, 그때는 ATR 로 내려간다.
    band: list[float] | None = None
    expected: float | None = None
    days: int | None = None
    model: str | None = None
    source: str = "manual"


class Sold(BaseModel):
    price: float = Field(gt=0)
    shares: float = Field(gt=0)
    reason: str = "target"


class Retarget(BaseModel):
    stop: float | None = Field(default=None, gt=0)
    targets: list[dict] | None = None


def _shape(position: Position, price: float | None) -> dict:
    unrealized = position.unrealized(price)
    return {
        "id": position.id,
        "provider": position.provider,
        "symbol": position.symbol,
        "label": names.label(position.symbol),
        "currency": position.currency,
        "entry": position.entry,
        "shares": position.shares,
        "sharesLeft": position.shares_left,
        "openedAt": position.opened_at,
        "note": position.note,
        "source": position.source,
        "band": position.band,
        "days": position.days,
        "stop": position.stop,
        "stopHitAt": position.stop_hit_at,
        "targets": position.targets,
        "realized": round(position.realized, 4),
        # 지금 값을 못 받았으면 **평가손익도 없다.** 마지막으로 받은 값으로 채우면
        # 언제 값인지 모르는 숫자가 화면에 남는다.
        "price": price,
        "unrealized": None if unrealized is None else round(unrealized, 4),
        "cost": round(position.cost, 4),
        "status": position.status,
        "closedAt": position.closed_at,
        "closeReason": position.close_reason,
        "pending": position.pending(),
        "events": position.events,
    }


@router.get("")
async def listing(closed: bool = True) -> dict:
    """장부 전체. 열린 것은 지금 값까지 붙여 준다."""
    items = all_positions(include_closed=closed)
    live = [p for p in items if p.status == OPEN]
    prices = await quotes.last_prices([(p.provider, p.symbol) for p in live])
    return {
        "positions": [_shape(p, prices.get((p.provider, p.symbol)))
                      for p in items],
        "record": record.summary(items),
        # 결정이 밀려 있으면 화면 맨 위에 뜬다. 이게 이 화면의 할 일이다.
        "waiting": sum(1 for p in live if p.pending()),
    }


@router.post("")
async def create(body: NewPosition) -> dict:
    """샀다고 적는다. 손절·목표는 밴드에서, 밴드가 없으면 변동성에서 나온다."""
    atr = None
    if not body.band:
        atr = await quotes.atr_pct(body.provider, body.symbol)
    position, why = manage.open_position(
        provider=body.provider, symbol=body.symbol, entry=body.entry,
        shares=body.shares, currency=currency_of(body.provider),
        band=body.band, expected=body.expected, atr_pct=atr,
        days=body.days, model=body.model, source=body.source, note=body.note)
    price = (await quotes.last_prices([(position.provider, position.symbol)])) \
        .get((position.provider, position.symbol))
    return {"position": _shape(position, price), "warning": why}


def _need(position_id: str) -> Position:
    found = get_position(position_id)
    if found is None:
        raise HTTPException(404, "그런 포지션이 없다")
    return found


@router.post("/{position_id}/sold")
def sold(position_id: str, body: Sold) -> dict:
    """실제로 판 만큼 덜어낸다. 다 팔면 닫힌다."""
    position = _need(position_id)
    if position.status == CLOSED:
        raise HTTPException(400, "이미 닫힌 포지션이다")
    return {"position": _shape(manage.sold(
        position, price=body.price, shares=body.shares, reason=body.reason), None)}


@router.post("/{position_id}/held")
def held(position_id: str) -> dict:
    """닿았는데 안 팔았다. 손절선을 올려 다시 건다."""
    position = _need(position_id)
    if position.status == CLOSED:
        raise HTTPException(400, "이미 닫힌 포지션이다")
    if not position.pending():
        raise HTTPException(400, "정할 것이 없다 — 닿은 값이 없다")
    return {"position": _shape(manage.held(position), None)}


@router.post("/{position_id}/close")
def close(position_id: str, reason: str = "manual") -> dict:
    position = _need(position_id)
    if position.status == CLOSED:
        raise HTTPException(400, "이미 닫힌 포지션이다")
    return {"position": _shape(manage.close(position, reason=reason), None)}


@router.patch("/{position_id}")
def retarget(position_id: str, body: Retarget) -> dict:
    position = _need(position_id)
    if position.status == CLOSED:
        raise HTTPException(400, "이미 닫힌 포지션이다")
    if body.stop is None and body.targets is None:
        raise HTTPException(400, "고칠 값이 없다")
    return {"position": _shape(manage.retarget(
        position, stop=body.stop, targets=body.targets), None)}


@router.delete("/{position_id}")
def drop(position_id: str) -> dict:
    """장부에서 지운다. **닫는 것과 다르다** — 잘못 적은 진입가를 물릴 때만 쓴다."""
    position = _need(position_id)
    if position.status == OPEN:
        # 먼저 닫아서 걸어 둔 알림을 거둔다. 안 그러면 장부에서 사라진 포지션의
        # 손절 알림이 계속 울린다.
        manage.close(position, reason="manual")
    return {"ok": remove_position(position_id)}


@router.get("/{position_id}/advice")
def next_step(position_id: str, days: int = 1) -> dict:
    """닫힌 뒤 할 일. 오늘 추천에 그 종목이 있으면 재진입 값까지 낸다."""
    position = _need(position_id)
    if position.status != CLOSED:
        raise HTTPException(400, "아직 안 닫힌 포지션이다")
    today = recommend_layer.today(position.provider, days)
    return advice.after_close(position, today)
