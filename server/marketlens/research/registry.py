"""근거 등록부의 뼈대.

이 프로그램의 예측은 전부 어딘가의 방법론에서 나온다. 그 출처를 주석에만 적어 두면
반년 뒤에 "이 숫자가 왜 이렇게 나왔는지"를 아무도 못 찾는다. 그래서 지표 카탈로그와
같은 방식으로 **표**를 만든다 — 화면에서 '이 예측의 근거'를 열어 볼 수 있어야 한다.

정직하게 쓴다:
- `confidence` 에 `contested` 가 있는 이유는, 기술적 분석의 예측력이 실제로 논쟁적이기
  때문이다. 다 강하다고 적으면 등록부가 장식이 된다.
- `limits` 는 비워 두지 말 것. 어떤 조건에서 통하지 않는지가 실전에서 더 중요하다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Confidence = Literal["strong", "moderate", "weak", "contested"]
Field = Literal[
    "analog", "event", "seasonality", "volatility",
    "uncertainty", "momentum", "validation", "indicator",
]

CONFIDENCE_LABELS: dict[Confidence, str] = {
    "strong": "재현이 잘 된 결과",
    "moderate": "여러 연구가 지지",
    "weak": "제한된 근거",
    "contested": "논쟁 중",
}

FIELD_LABELS: dict[Field, str] = {
    "analog": "유사구간 예측",
    "event": "이벤트 스터디",
    "seasonality": "계절성·캘린더",
    "volatility": "변동성",
    "uncertainty": "구간 추정",
    "momentum": "모멘텀·평균회귀",
    "validation": "검증 방법",
    "indicator": "지표 원전",
}


@dataclass(frozen=True)
class Source:
    title: str
    authors: str
    year: int
    venue: str = ""
    url: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "venue": self.venue,
            "url": self.url,
        }


@dataclass(frozen=True)
class Evidence:
    key: str
    field: Field
    claim: str                    # 한 문장 주장. 화면에 그대로 나간다.
    effect: str                   # 보고된 효과의 크기. 숫자가 있으면 숫자로.
    limits: str                   # 통하지 않는 조건. 여기가 제일 중요하다.
    confidence: Confidence
    used_by: tuple[str, ...]      # 이 근거에 기대는 코드 경로
    sources: tuple[Source, ...]

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "field": self.field,
            "fieldLabel": FIELD_LABELS[self.field],
            "claim": self.claim,
            "effect": self.effect,
            "limits": self.limits,
            "confidence": self.confidence,
            "confidenceLabel": CONFIDENCE_LABELS[self.confidence],
            "usedBy": list(self.used_by),
            "sources": [s.to_dict() for s in self.sources],
        }


_ENTRIES: dict[str, Evidence] = {}


def add(entry: Evidence) -> Evidence:
    if entry.key in _ENTRIES:
        raise RuntimeError(f"근거 키가 겹친다: {entry.key}")
    if not entry.limits.strip():
        raise ValueError(f"{entry.key}: limits 를 비워 두지 말 것")
    _ENTRIES[entry.key] = entry
    return entry


def get(key: str) -> Evidence:
    try:
        return _ENTRIES[key]
    except KeyError:
        raise KeyError(f"등록되지 않은 근거: {key!r}") from None


def all_entries() -> list[Evidence]:
    return sorted(_ENTRIES.values(), key=lambda e: (e.field, e.key))


def cite(*keys: str) -> list[dict]:
    """예측 응답에 붙일 근거 목록. 없는 키를 쓰면 여기서 걸린다."""
    return [get(key).to_dict() for key in keys]
