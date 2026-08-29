"""들고 있는 것. 진입가와 주수를 받아 팔 때까지 따라간다.

## 왜 알림 옆에 사나

포지션과 알림은 한 몸이다. 포지션이 생기면 손절·목표 알림이 같이 걸리고, 그 알림이
울리면 포지션이 움직인다. 그래서 파일도 알림과 같은 폴더에 둔다 — 배포에서 볼륨을
두 번 잡지 않아도 되고, 테스트가 폴더 하나만 옮기면 둘 다 격리된다.

    alerts/positions.json    들고 있는 것과 닫은 것

## 자동으로 팔지 않는다

**닿았다고 체결로 치지 않는다.** 이건 모의가 아니라 실제로 산 것을 따라가는 장부라,
값이 닿아도 사람이 실제로 팔았는지는 프로그램이 모른다. 알림은 "닿았다"까지 적고
멈추며, 「팔았다」·「안 팔았다」는 사람이 누른다. 그 두 갈래가 곧 부분 익절과
트레일링 스탑이다 — 안 팔았다고 하면 손절가를 올려 다시 건다.

## 통화를 지어내지 않는다

`currency` 는 그 시장이 정한다. 업비트·국내주식은 원화, 바이낸스·미국주식은 달러다.
**환율로 환산하지 않는다** — `screen/coins.py` 가 적어 둔 것과 같은 이유로, 곱해서
나온 값에는 살 수도 팔 수도 없다. 코인을 원화로 보고 싶으면 원화 마켓(`KRW-SOL`)으로
포지션을 연다. 그게 실제로 치른 값이다.
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..alerts import store as alerts

# 알림 파일과 같은 잠금을 쓰지 않는다. 서로 다른 파일이고, 알림 잠금을 빌리면
# 포지션을 쓰는 동안 감시 루프가 규칙을 못 읽는다.
_lock = threading.Lock()

KRW_PROVIDERS = frozenset({"upbit", "toss_kr", "kis"})
OPEN, CLOSED = "open", "closed"
# 왜 닫혔나. 사람이 손으로 닫은 것과 손절로 닫힌 것은 성적표에서 다르게 읽힌다.
REASONS = ("target", "stop", "manual")


def path() -> Path:
    """읽을 때마다 계산한다. 모듈 로드 때 붙잡으면 테스트가 폴더를 못 옮긴다."""
    return alerts.FOLDER / "positions.json"


def currency_of(provider: str) -> str:
    return "KRW" if provider in KRW_PROVIDERS else "USD"


@dataclass
class Position:
    provider: str
    symbol: str
    entry: float
    shares: float
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    currency: str = "KRW"
    opened_at: str = field(default_factory=alerts.now)
    note: str = ""
    source: str = "manual"                # manual | recommend

    # 계획이 딛고 선 근거. 화면이 "왜 이 값인가"를 말할 수 있어야 한다.
    band: list[float] | None = None
    days: int | None = None
    model: str | None = None

    # 손절 하나와 목표 여럿. 목표는 각각 몫(`portion`)을 들고 있다.
    stop: float = 0.0
    stop_rule_id: str | None = None
    # 손절은 하나뿐이라 목표처럼 목록으로 두지 않는다. 대신 두 시각을 들고 있다 —
    # 닿은 때와, 사람이 팔았는지 정한 때.
    stop_hit_at: str | None = None
    stop_settled_at: str | None = None
    targets: list[dict] = field(default_factory=list)

    # **0 을 기본값으로 두면 안 된다.** 다 판 판은 남은 주수가 진짜 0 인데, 0 을
    # "안 적혔다"로 읽으면 다시 읽을 때마다 판 주식이 되살아난다. 그래서 음수를
    # 자리표시자로 쓴다 — 주수는 음수가 될 수 없다.
    shares_left: float = -1.0
    realized: float = 0.0                 # 실현 손익. 통화는 `currency`.
    events: list[dict] = field(default_factory=list)

    status: str = OPEN
    closed_at: str | None = None
    close_reason: str | None = None

    def __post_init__(self) -> None:
        if self.shares_left < 0:
            self.shares_left = self.shares

    @property
    def cost(self) -> float:
        """아직 들고 있는 몫의 원금."""
        return self.entry * self.shares_left

    def unrealized(self, price: float | None) -> float | None:
        if price is None or self.shares_left <= 0:
            return None
        return (price - self.entry) * self.shares_left

    def pending(self) -> list[dict]:
        """닿았는데 아직 사람이 안 정한 것. 화면이 물어볼 자리다."""
        out = [t for t in self.targets if t.get("hitAt") and not t.get("settledAt")]
        if self.status == OPEN and self.stop_hit_at and not self.stop_settled_at:
            out = [*out, {"kind": "stop", "price": self.stop,
                          "hitAt": self.stop_hit_at}]
        return out


def _read() -> list[dict]:
    file = path()
    if not file.is_file():
        return []
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return raw if isinstance(raw, list) else []


def _write(rows: list[dict]) -> None:
    alerts.FOLDER.mkdir(parents=True, exist_ok=True)
    file = path()
    # 알림과 같은 방식. 쓰는 도중에 죽으면 장부가 반쪽으로 남는다.
    temporary = file.with_suffix(file.suffix + ".tmp")
    temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(file)


def _shape(item: dict) -> Position | None:
    fields = {k: v for k, v in item.items() if k in Position.__dataclass_fields__}
    try:
        return Position(**fields)
    except TypeError:
        return None                       # 모양이 바뀐 옛 항목은 건너뛴다


def all_positions(include_closed: bool = True) -> list[Position]:
    """최근에 연 것이 위로."""
    out = [p for p in (_shape(i) for i in _read()) if p is not None]
    if not include_closed:
        out = [p for p in out if p.status == OPEN]
    return sorted(out, key=lambda p: p.opened_at, reverse=True)


def get(position_id: str) -> Position | None:
    for found in all_positions():
        if found.id == position_id:
            return found
    return None


def save(position: Position) -> Position:
    with _lock:
        rows = _read()
        payload = asdict(position)
        for index, item in enumerate(rows):
            if item.get("id") == position.id:
                rows[index] = payload
                break
        else:
            rows.append(payload)
        _write(rows)
    return position


def remove(position_id: str) -> bool:
    """장부에서 지운다. **닫는 것과 다르다** — 닫은 것은 성적표에 남고 이건 안 남는다.
    잘못 적은 진입가를 물릴 때만 쓴다."""
    with _lock:
        rows = _read()
        left = [r for r in rows if r.get("id") != position_id]
        if len(left) == len(rows):
            return False
        _write(left)
        return True


def note_event(position: Position, kind: str, text: str, **extra) -> None:
    """무슨 일이 있었는지 한 줄. **지우지 않는다** — 나중에 왜 이렇게 됐는지 읽는다."""
    position.events.append({"at": alerts.now(), "kind": kind, "text": text, **extra})
