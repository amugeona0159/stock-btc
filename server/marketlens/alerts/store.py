"""알림 규칙과 받은 알림을 담아 둔다.

파일 두 개다. DB 를 들이지 않는다 — 규칙은 수십 개짜리고, 이 저장소는 이미
`learning/` 을 JSON 으로 다루는 방식을 쓰고 있다.

    alerts/rules.json      걸어 둔 규칙
    alerts/fired.jsonl     실제로 나간 알림 (한 줄에 하나, 지우지 않는다)

**나간 알림을 지우지 않는 이유**: "그때 알림이 왔었나" 를 나중에 확인할 수 있어야
한다. 알림이 맞았는지 틀렸는지가 이 기능의 유일한 성적표인데, 화면에서 지웠다고
기록까지 지우면 그걸 영영 못 잰다. 화면에서 치우는 것은 `archived` 표시로 한다.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FOLDER = Path(os.environ.get("MARKET_LENS_ALERTS") or (ROOT / "alerts"))
if not FOLDER.is_absolute():
    FOLDER = ROOT / FOLDER
RULES = FOLDER / "rules.json"
FIRED = FOLDER / "fired.jsonl"
SUBS = FOLDER / "subscriptions.json"

# 파일 하나를 감시 루프와 HTTP 요청이 같이 만진다. 쓰는 동안 읽으면 반쪽 JSON 을
# 읽게 되므로 잠근다. 프로세스가 하나라 이걸로 충분하다.
_lock = threading.Lock()

KINDS = ("buy_below", "sell_above", "stop_below", "target_above")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Rule:
    """가격 하나를 지켜보는 규칙.

    `kind` 가 방향을 정한다 — `*_below` 는 **내려와서 닿을 때**, `*_above` 는
    **올라가서 닿을 때** 다. 같은 가격이라도 어느 쪽에서 오느냐가 다르다.
    """

    provider: str
    symbol: str
    kind: str
    price: float
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    note: str = ""
    source: str = "manual"          # manual | recommend
    active: bool = True
    fired_at: str | None = None
    created_at: str = field(default_factory=now)
    # 그 규칙이 딛고 선 근거. 알림에 그대로 실어 보낸다.
    band: list[float] | None = None
    days: int | None = None

    def hits(self, price: float) -> bool:
        if self.kind in ("buy_below", "stop_below"):
            return price <= self.price
        return price >= self.price


def _read(path: Path, fallback):
    if not path.is_file():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def _write(path: Path, payload) -> None:
    FOLDER.mkdir(parents=True, exist_ok=True)
    # 임시 파일에 쓰고 갈아끼운다. 쓰는 도중에 죽으면 원본이 반쪽으로 남는다.
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def rules() -> list[Rule]:
    raw = _read(RULES, [])
    out = []
    for item in raw:
        fields = {k: v for k, v in item.items() if k in Rule.__dataclass_fields__}
        try:
            out.append(Rule(**fields))
        except TypeError:
            continue                 # 모양이 바뀐 옛 항목은 조용히 건너뛴다
    return out


def save_rules(items: list[Rule]) -> None:
    with _lock:
        _write(RULES, [asdict(r) for r in items])


def add(rule: Rule) -> Rule:
    """같은 종목·같은 종류·같은 가격이 이미 있으면 새로 만들지 않는다.

    아침마다 추천이 규칙을 만드는데, 추천이 이틀 연속 같으면 규칙이 두 개가 된다 —
    그러면 알림도 두 번 온다.
    """
    with _lock:
        items = rules()
        for existing in items:
            same = (existing.provider == rule.provider
                    and existing.symbol == rule.symbol
                    and existing.kind == rule.kind
                    and abs(existing.price - rule.price) < 1e-9
                    and existing.active)
            if same:
                return existing
        items.append(rule)
        _write(RULES, [asdict(r) for r in items])
    return rule


def update(rule_id: str, **changes) -> Rule | None:
    with _lock:
        items = rules()
        found = None
        for rule in items:
            if rule.id == rule_id:
                for key, value in changes.items():
                    if key in Rule.__dataclass_fields__:
                        setattr(rule, key, value)
                found = rule
        if found is not None:
            _write(RULES, [asdict(r) for r in items])
        return found


def remove(rule_id: str) -> bool:
    with _lock:
        items = rules()
        left = [r for r in items if r.id != rule_id]
        if len(left) == len(items):
            return False
        _write(RULES, [asdict(r) for r in left])
        return True


def record_fired(entry: dict) -> None:
    """나간 알림을 한 줄 남긴다. **덮어쓰지 않는다.**"""
    with _lock:
        FOLDER.mkdir(parents=True, exist_ok=True)
        with FIRED.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def fired(limit: int = 200) -> list[dict]:
    if not FIRED.is_file():
        return []
    rows = []
    for line in FIRED.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows[-limit:][::-1]           # 최근 것이 위로


def log(*, since: str | None = None, symbol: str | None = None,
        kind: str | None = None, include_archived: bool = True,
        limit: int | None = None) -> list[dict]:
    """조건에 맞는 기록. 최근 것이 위로.

    `fired()` 는 알림함이 쓰는 함수라 최근 200건에서 끊는다. 기록은 **끊으면 기록이
    아니다** — 여기서는 상한 없는 것이 기본이고, 화면이 원할 때만 limit 을 준다.

    `since` 를 문자열 그대로 비교하는 건 `at` 이 전부 `now()` 가 만든 같은 모양
    (초 단위 UTC ISO)이라서다. 줄마다 파싱하면 비용만 붙고 얻는 게 없다.
    """
    if not FIRED.is_file():
        return []
    rows = []
    for line in FIRED.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if since and str(row.get("at") or "") < since:
            continue
        if symbol and row.get("symbol") != symbol:
            continue
        if kind and row.get("kind") != kind:
            continue
        if not include_archived and row.get("archived"):
            continue
        rows.append(row)
    rows.reverse()                       # 최근 것이 위로
    return rows[:limit] if limit else rows


def count() -> int:
    """남아 있는 기록 줄 수. 걸러낸 화면이 "뭘 숨겼는지" 말할 수 있어야 한다."""
    if not FIRED.is_file():
        return 0
    return sum(1 for line in FIRED.read_text(encoding="utf-8").splitlines()
               if line.strip())


def mark(entry_id: str, **changes) -> bool:
    """읽음·보관 표시. 줄을 지우지 않고 다시 써서 상태만 바꾼다."""
    if not FIRED.is_file():
        return False
    with _lock:
        rows = []
        touched = False
        for line in FIRED.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("id") == entry_id:
                row.update(changes)
                touched = True
            rows.append(row)
        if touched:
            FIRED.write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                encoding="utf-8")
        return touched


# --- 푸시 구독 -------------------------------------------------------------

def subscriptions() -> list[dict]:
    return _read(SUBS, [])


def subscribe(item: dict) -> None:
    """같은 엔드포인트는 한 번만. 폰에서 새로고침할 때마다 쌓이면 알림이 여러 번 온다."""
    with _lock:
        items = [s for s in subscriptions() if s.get("endpoint") != item.get("endpoint")]
        items.append(item)
        _write(SUBS, items)


def unsubscribe(endpoint: str) -> None:
    with _lock:
        _write(SUBS, [s for s in subscriptions() if s.get("endpoint") != endpoint])
