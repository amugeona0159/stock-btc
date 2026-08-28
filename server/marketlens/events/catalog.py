"""사건 사전 + 캘린더에서 계산되는 반복 일정.

두 갈래를 여기서 합친다:
- `seed_events.json` — 사람이 큐레이션한 일회성 사건. 날짜가 확실한 것만 넣는다.
- 반복 일정 — 월말·분기말·옵션 만기처럼 **달력에서 계산되는** 것. 이건 저장하지 않는다.
  저장하면 언젠가 표가 밀리고, 계산은 절대 밀리지 않는다.

FOMC 처럼 날짜가 정해져 있지만 달력에서 계산되지 않는 일정은 여기 박지 않는다 —
연도마다 바뀌어서 하드코딩하면 반드시 틀린다. 그런 건 뉴스 소스나 직접 등록으로 받는다.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

from .schema import Event

SEED_PATH = Path(__file__).with_name("seed_events.json")


@lru_cache(maxsize=1)
def seed() -> tuple[Event, ...]:
    raw = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    events = []
    for item in raw.get("events", []):
        events.append(Event(
            ts=int(pd.Timestamp(item["at"]).timestamp() * 1000),
            kind=item["kind"],
            title=item["title"],
            source="seed",
            scope=item.get("scope", "global"),
            severity=float(item.get("severity", 0.5)),
            scheduled=bool(item.get("scheduled", False)),
            url=item.get("url", ""),
            tags=tuple(item.get("tags", ())),
            note=item.get("note", ""),
        ))
    return tuple(sorted(events, key=lambda e: e.ts))


def _third_friday(year: int, month: int) -> pd.Timestamp:
    first = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
    # 첫 금요일까지 밀고 2주 더한다.
    offset = (4 - first.weekday()) % 7
    return first + pd.Timedelta(days=offset + 14)


def _last_friday(year: int, month: int) -> pd.Timestamp:
    last = pd.Timestamp(year=year, month=month, day=1, tz="UTC") + pd.offsets.MonthEnd(0)
    return last - pd.Timedelta(days=(last.weekday() - 4) % 7)


def recurring(start_ts: int, end_ts: int) -> list[Event]:
    """구간 안의 반복 일정. 달력만으로 정해지는 것들이다."""
    start = pd.Timestamp(start_ts, unit="ms", tz="UTC")
    end = pd.Timestamp(end_ts, unit="ms", tz="UTC")
    if end < start:
        return []

    out: list[Event] = []

    def push(when: pd.Timestamp, title: str, severity: float, tags: tuple[str, ...]) -> None:
        if start <= when <= end:
            out.append(Event(
                ts=int(when.timestamp() * 1000),
                kind="calendar",
                title=title,
                source="calendar",
                scope="global",
                severity=severity,
                scheduled=True,
                tags=tags,
            ))

    months = pd.date_range(start.normalize() - pd.offsets.MonthBegin(1),
                           end.normalize() + pd.offsets.MonthBegin(1),
                           freq="MS", tz="UTC")
    for month_start in months:
        year, month = int(month_start.year), int(month_start.month)
        month_end = month_start + pd.offsets.MonthEnd(0)

        push(month_end, "월말", 0.3, ("month-end", "rebalance"))
        push(month_start, "월초", 0.25, ("month-start",))
        # 월간 옵션 만기. 주식은 셋째 금요일이 관행이고 암호화폐 파생도 대체로 금요일에 몰린다.
        push(_third_friday(year, month), "월간 옵션 만기(셋째 금요일)", 0.4, ("expiry", "options"))

        if month in (3, 6, 9, 12):
            push(month_end, "분기말", 0.5, ("quarter-end", "rebalance", "window-dressing"))
            push(_last_friday(year, month), "분기 선물·옵션 만기", 0.55, ("expiry", "quarterly"))

    return sorted(out, key=lambda e: e.ts)


def builtin(start_ts: int, end_ts: int) -> list[Event]:
    """구간에 걸리는 내장 사건 전부 (사전 + 반복 일정)."""
    curated = [e for e in seed() if start_ts <= e.ts <= end_ts]
    return sorted(curated + recurring(start_ts, end_ts), key=lambda e: e.ts)
