"""가격을 지켜보다 조건에 닿으면 알린다.

폰은 꺼져 있다. 그러니 **서버가 보고 있어야** 알림이 나간다. FastAPI 가 뜰 때 이
루프 하나가 같이 돌고, 규칙에 걸린 종목만 주기적으로 시세를 읽는다.

## 알림 앱을 못 쓰게 만드는 것들 — 여기서 막는다

1. **폭주.** 가격이 경계에서 떨면 알림이 초당 몇 번씩 온다. 한 규칙은 **한 번만**
   쏘고(`fired_at`), 다시 쏘려면 사람이 되살려야 한다.
2. **한밤중.** 조용한 시간에는 급하지 않은 것을 모아 뒀다 아침에 한 번 보낸다.
   손절(`stop_below`)만 예외다 — 그건 자고 있어도 알아야 한다.
3. **죽은 루프.** 프로바이더 하나가 실패했다고 루프가 멈추면 나머지 알림도 같이
   죽는다. 종목 단위로 감싸고, 연달아 실패하면 그 종목만 잠시 쉰다.

## 무엇을 '지금 가격'으로 보나

마지막 봉의 **종가**가 아니라 **고가/저가**까지 본다. 30초에 한 번 보는데 종가만
보면 그 사이에 찍고 돌아온 값을 놓친다. `buy_below` 는 저가로, `sell_above` 는
고가로 판정한다 — 실제로 그 가격이 시장에 있었기 때문이다.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..providers import get as get_provider
from ..screen import names
from . import store
from .store import Rule

log = logging.getLogger("marketlens.alerts")

SEOUL = ZoneInfo("Asia/Seoul")
# 암호화폐는 24시간 돈다. 주식은 장중에만 움직이니 덜 자주 봐도 된다.
PERIOD = {"crypto": 30.0, "kr": 60.0, "us": 60.0}
DEFAULT_PERIOD = 60.0
# 조용한 시간(KST). 이 사이에는 손절 말고는 모아 둔다.
QUIET = (23, 7)
# 한 종목이 연달아 실패하면 이만큼 쉬었다 다시 본다.
COOLDOWN = 300.0


def quiet_now(at: datetime | None = None) -> bool:
    hour = (at or datetime.now(SEOUL)).hour
    start, end = QUIET
    return hour >= start or hour < end


def _label(symbol: str) -> str:
    found = names.of(symbol)
    return f"{found} {names.ticker(symbol)}" if found else symbol


def message(rule: Rule, price: float) -> dict:
    """알림 문구.

    **"사세요" 라고 쓰지 않는다.** 이 도구가 아는 것은 "설정한 값에 닿았다" 까지고,
    살지 말지는 사람이 정한다. 방향 적중이 55% 인 모델로 명령형을 쓰면 안 된다.
    """
    what = _label(rule.symbol)
    head = {
        "buy_below": f"{what} · 매수 지켜보던 값에 닿았다",
        "sell_above": f"{what} · 매도 지켜보던 값에 닿았다",
        "target_above": f"{what} · 목표가 도달",
        "stop_below": f"{what} · 손절선 이탈",
    }.get(rule.kind, f"{what} · 알림")

    lines = [f"지금 {price:,.4g} (설정 {rule.price:,.4g})"]
    if rule.band and len(rule.band) == 2 and rule.days:
        lines.append(f"{rule.days}일 안에 {rule.band[0]:+.1f}% ~ {rule.band[1]:+.1f}% "
                     f"안에 있을 확률 80%")
    if rule.note:
        lines.append(rule.note)
    return {"title": head, "body": " · ".join(lines)}


async def latest_price(rule: Rule) -> tuple[float, float] | None:
    """(저가, 고가). 마지막 두 봉을 본다 — 방금 닫힌 봉을 놓치지 않으려고."""
    provider = get_provider(rule.provider)
    frame = await provider.history(rule.symbol, "1m", 2)
    if frame is None or frame.empty:
        return None
    return float(frame["low"].min()), float(frame["high"].max())


def touched(rule: Rule, low: float, high: float) -> float | None:
    """닿았으면 그때의 가격. 종가가 아니라 **봉이 실제로 찍은 값**으로 본다."""
    if rule.kind in ("buy_below", "stop_below"):
        return low if low <= rule.price else None
    return high if high >= rule.price else None


class Watcher:
    """규칙을 지켜보는 루프 하나."""

    def __init__(self, send) -> None:
        self._send = send                     # 알림을 실제로 내보내는 함수
        self._task: asyncio.Task | None = None
        self._rest: dict[str, float] = {}     # 실패한 종목의 쉬는 시각
        self._held: list[dict] = []           # 조용한 시간에 모아 둔 것

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="alert-watch")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:                 # noqa: BLE001
                # **루프는 절대 죽지 않는다.** 하나가 터져도 다음 주기에 다시 돈다.
                log.exception("알림 감시 한 바퀴 실패")
            await asyncio.sleep(min(PERIOD.values()))

    async def tick(self) -> None:
        """한 바퀴. 테스트가 이 함수만 부른다."""
        active = [r for r in store.rules() if r.active and r.fired_at is None]
        if not active:
            await self._release()
            return

        loop = asyncio.get_running_loop()
        for rule in active:
            key = f"{rule.provider}:{rule.symbol}"
            if self._rest.get(key, 0.0) > loop.time():
                continue
            try:
                got = await latest_price(rule)
            except Exception as exc:          # noqa: BLE001
                # 그 종목만 쉰다. 나머지 알림은 계속 돈다.
                self._rest[key] = loop.time() + COOLDOWN
                log.warning("시세 실패 %s — %s", key, str(exc)[:80])
                continue
            self._rest.pop(key, None)
            if got is None:
                continue
            price = touched(rule, *got)
            if price is None:
                continue
            await self._fire(rule, price)
        await self._release()

    async def _fire(self, rule: Rule, price: float) -> None:
        payload = message(rule, price)
        entry = {
            "id": f"{rule.id}-{store.now()}",
            "ruleId": rule.id, "provider": rule.provider, "symbol": rule.symbol,
            "kind": rule.kind, "price": price, "setPrice": rule.price,
            "at": store.now(), "read": False, "archived": False,
            **payload,
        }
        # **먼저 표시하고 보낸다.** 보내기가 느려서 다음 주기가 돌면 두 번 나간다.
        store.update(rule.id, fired_at=store.now())
        store.record_fired(entry)

        urgent = rule.kind == "stop_below"
        if quiet_now() and not urgent:
            self._held.append(entry)          # 자는 동안은 모아 둔다
            return
        await self._deliver(entry)

    async def _release(self) -> None:
        """조용한 시간이 끝나면 모아 둔 것을 한 번에 보낸다."""
        if not self._held or quiet_now():
            return
        held, self._held = self._held, []
        if len(held) == 1:
            await self._deliver(held[0])
            return
        await self._deliver({
            "id": f"batch-{store.now()}",
            "title": f"밤사이 알림 {len(held)}건",
            "body": " / ".join(h["title"] for h in held[:4]),
            "at": store.now(), "read": False, "archived": False, "batch": True,
        })

    async def _deliver(self, entry: dict) -> None:
        try:
            await self._send(entry)
        except Exception:                     # noqa: BLE001
            log.exception("알림 전송 실패 — 기록은 남았다")


def from_recommendation(provider: str, days: int, body: dict) -> list[Rule]:
    """아침 추천을 규칙으로 바꾼다.

    **추천 하나에 규칙 두 개**를 만든다 — 살 값(밴드 하단)과 팔 값(기대값).
    "지금 사라" 가 아니라 "이 값이 오면 알려 준다" 로 두는 것이 요점이다.
    """
    made: list[Rule] = []
    for item in body.get("buy", []):
        last = item.get("last")
        band = item.get("band")
        if not last or not band or len(band) != 2:
            continue
        note = f"{body.get('date', '')} 추천 · 기대 {item.get('expected', 0):+.2f}%"
        made.append(Rule(provider=provider, symbol=item["symbol"], kind="buy_below",
                         price=float(last) * (1 + band[0] / 100.0),
                         note=note, source="recommend", band=band, days=days))
        made.append(Rule(provider=provider, symbol=item["symbol"], kind="target_above",
                         price=float(last) * (1 + (item.get("expected") or 0) / 100.0),
                         note=note, source="recommend", band=band, days=days))
    return [store.add(r) for r in made]


def as_dict(rule: Rule) -> dict:
    out = asdict(rule)
    out["label"] = _label(rule.symbol)
    return out


def _utc_hour() -> int:
    return datetime.now(timezone.utc).hour
