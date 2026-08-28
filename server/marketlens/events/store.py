"""직접 등록한 사건의 저장소.

파일 하나(JSON)에 append 한다. 사용자가 넣은 건 어떤 API 보다 정확한 자료라
지워지면 안 되고, 손으로 열어 고칠 수 있어야 한다.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pandas as pd

from .schema import Event

PATH = Path("store_data/user_events.json")
_lock = threading.Lock()


def _read() -> list[dict]:
    if not PATH.is_file():
        return []
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # 파일이 깨졌다고 앱이 죽지는 않는다. 다만 조용히 덮어쓰지도 않는다.
        return []


def _write(rows: list[dict]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def to_event(row: dict) -> Event:
    at = row.get("at")
    ts = int(row["ts"]) if row.get("ts") else int(pd.Timestamp(at).timestamp() * 1000)
    return Event(
        ts=ts,
        kind=row.get("kind", "user"),
        title=row["title"],
        source="user",
        scope=row.get("scope", "global"),
        severity=float(row.get("severity", 0.5)),
        scheduled=bool(row.get("scheduled", False)),
        url=row.get("url", ""),
        tags=tuple(row.get("tags", ())),
        note=row.get("note", ""),
    )


def add(row: dict) -> Event:
    event = to_event(row)
    with _lock:
        rows = _read()
        stored = {**row, "ts": event.ts, "id": event.id}
        rows = [r for r in rows if r.get("id") != event.id] + [stored]
        _write(sorted(rows, key=lambda r: r["ts"]))
    return event


def remove(event_id: str) -> bool:
    with _lock:
        rows = _read()
        kept = [r for r in rows if r.get("id") != event_id]
        if len(kept) == len(rows):
            return False
        _write(kept)
    return True


def all_events() -> list[Event]:
    return [to_event(r) for r in _read()]


def between(start_ts: int, end_ts: int) -> list[Event]:
    return [e for e in all_events() if start_ts <= e.ts <= end_ts]
