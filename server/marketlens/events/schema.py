"""이벤트 표준형.

출처가 여섯 갈래다 — 큐레이션된 사전, 캘린더에서 계산되는 반복 사건, 차트 자체 사건,
뉴스(GDELT·Finnhub), 매크로 지표(FRED), 사용자 등록. 전부 이 한 형태로 들어온다.
출처마다 다른 모양을 쓰면 이벤트 스터디가 출처별 분기로 뒤덮인다.

`scheduled` 를 따로 두는 이유: 예정된 발표(FOMC·실적)와 돌발 사건은 성질이 다르다.
예정된 건 이미 상당 부분 가격에 들어가 있어서 **방향이 아니라 변동성**만 신뢰할 수 있다
(research.library: scheduled_macro_event_risk).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

# 사건의 종류. 이벤트 스터디를 종류별로 묶을 때 쓴다.
KINDS = {
    "macro": "거시",
    "regulation": "규제·정책",
    "crypto": "암호화폐 사건",
    "company": "기업",
    "chart": "차트 사건",
    "calendar": "반복 일정",
    "news": "뉴스",
    "user": "직접 등록",
}

SOURCES = {
    "seed": "내장 사전",
    "calendar": "캘린더 계산",
    "detector": "차트 탐지",
    "gdelt": "GDELT",
    "finnhub": "Finnhub 뉴스",
    "fred": "FRED",
    "user": "직접 등록",
}


@dataclass(frozen=True)
class Event:
    ts: int                      # UTC ms. 사건이 알려진 시각.
    kind: str
    title: str
    source: str
    scope: str = "global"        # global | market:<crypto|us|kr> | symbol:<SYMBOL>
    severity: float = 0.5        # 0..1. 사전은 사람이 매기고, 탐지는 크기에서 계산한다.
    scheduled: bool = False
    url: str = ""
    tags: tuple[str, ...] = ()
    note: str = ""

    @property
    def id(self) -> str:
        """같은 사건이 여러 출처로 들어와도 하나로 접히게 하는 열쇠."""
        raw = f"{self.ts // 3_600_000}|{self.scope}|{self.title.strip().lower()[:60]}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def applies_to(self, symbol: str, market: str) -> bool:
        if self.scope == "global":
            return True
        target, _, value = self.scope.partition(":")
        if target == "market":
            return value == market
        if target == "symbol":
            return value.upper() == symbol.upper()
        return False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ts": self.ts,
            "kind": self.kind,
            "kindLabel": KINDS.get(self.kind, self.kind),
            "title": self.title,
            "source": self.source,
            "sourceLabel": SOURCES.get(self.source, self.source),
            "scope": self.scope,
            "severity": round(self.severity, 3),
            "scheduled": self.scheduled,
            "url": self.url,
            "tags": list(self.tags),
            "note": self.note,
        }


@dataclass
class EventSet:
    """한 심볼에 걸리는 사건 모음. 중복을 접고 시간순으로 들고 있는다."""

    events: list[Event] = field(default_factory=list)

    def add(self, *incoming: Event) -> "EventSet":
        seen = {e.id for e in self.events}
        for event in incoming:
            if event.id in seen:
                continue
            seen.add(event.id)
            self.events.append(event)
        self.events.sort(key=lambda e: e.ts)
        return self

    def for_symbol(self, symbol: str, market: str) -> list[Event]:
        return [e for e in self.events if e.applies_to(symbol, market)]

    def between(self, start_ts: int, end_ts: int) -> list[Event]:
        return [e for e in self.events if start_ts <= e.ts <= end_ts]

    def by_kind(self, *kinds: str) -> list[Event]:
        wanted = set(kinds)
        return [e for e in self.events if e.kind in wanted]

    def __len__(self) -> int:
        return len(self.events)
