"""시나리오 — 질문을 기계가 다룰 수 있는 조건으로 바꾼 것.

자연어로 물어도, 폼으로 골라도 결국 **같은 이 구조**가 된다. 그래야 사람이 해석을 보고
고칠 수 있다. LLM 이 바로 답을 내면 왜 그 답이 나왔는지 검증할 방법이 없다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from ..core.timeframe import to_ms
from ..events.schema import KINDS

# 자주 쓰는 기간. 화면의 빠른 선택지이기도 하다.
HORIZON_PRESETS = {
    "1일": 24, "3일": 72, "1주일": 168, "2주일": 336, "1개월": 720, "3개월": 2160,
}

REGIME_VOLATILITY = {0: "저변동", 1: "보통", 2: "고변동"}
REGIME_TREND = {-1: "하락추세", 0: "횡보", 1: "상승추세"}


class ScenarioDraft(BaseModel):
    """LLM 이 채우는 형태. 필드마다 무엇을 뜻하는지가 곧 프롬프트다."""

    horizon_hours: float = Field(
        168.0, ge=1.0, le=8760.0,
        description="며칠/몇 주 뒤를 묻는지를 시간으로. '일주일 이내'면 168.",
    )
    horizon_text: str = Field("1주일", description="사람이 읽는 기간 표현")
    event_kinds: list[str] = Field(
        default_factory=list,
        description=f"조건으로 걸 사건 종류. 가능한 값: {sorted(KINDS)}",
    )
    event_tags: list[str] = Field(
        default_factory=list,
        description="사건 태그로 좁히기. 예: rate, etf, regulation, hack, halving, crash",
    )
    require_volatility: int | None = Field(
        None, description="변동성 레짐 조건. 0=저변동 1=보통 2=고변동, 조건 없으면 null",
    )
    require_trend: int | None = Field(
        None, description="추세 레짐 조건. -1=하락 0=횡보 1=상승, 조건 없으면 null",
    )
    emphasis: list[str] = Field(
        default_factory=list,
        description="무겁게 볼 상황 축. trend momentum position volatility volume regime calendar",
    )
    context_weight: float = Field(
        0.5, ge=0.0, le=1.0,
        description="모양(0)과 상황(1) 중 어디에 무게를 둘지. 기본 0.5",
    )
    direction_hint: int | None = Field(
        None, description="질문이 방향을 전제하면 1/-1, 아니면 null. 예측을 바꾸지는 않는다",
    )
    interpretation: str = Field(
        "", description="질문을 어떻게 읽었는지 한 문장. 사람이 확인하고 고칠 수 있게.",
    )


@dataclass
class Scenario:
    """엔진이 실제로 쓰는 형태. 봉 수로 환산된 기간이 들어 있다."""

    question: str
    timeframe: str
    horizon: int                       # 봉 수
    horizon_hours: float
    horizon_text: str
    event_kinds: tuple[str, ...] = ()
    event_tags: tuple[str, ...] = ()
    require_volatility: int | None = None
    require_trend: int | None = None
    group_weights: dict[str, float] = field(default_factory=dict)
    context_weight: float = 0.5
    direction_hint: int | None = None
    interpretation: str = ""
    parsed_by: str = "rule"            # rule | llm | form
    notes: list[str] = field(default_factory=list)

    @staticmethod
    def from_draft(
        draft: ScenarioDraft, question: str, timeframe: str, parsed_by: str,
        max_horizon: int = 400,
    ) -> "Scenario":
        step_hours = to_ms(timeframe) / 3_600_000
        bars = max(1, round(draft.horizon_hours / step_hours))
        notes: list[str] = []
        if bars > max_horizon:
            notes.append(
                f"{draft.horizon_text} 는 {timeframe} 봉으로 {bars}개다 — "
                f"{max_horizon}개로 잘랐다. 더 긴 기간은 굵은 봉으로 보는 게 맞다."
            )
            bars = max_horizon

        weights = {key: 1.8 for key in draft.emphasis if key}
        return Scenario(
            question=question,
            timeframe=timeframe,
            horizon=bars,
            horizon_hours=draft.horizon_hours,
            horizon_text=draft.horizon_text,
            event_kinds=tuple(k for k in draft.event_kinds if k in KINDS),
            event_tags=tuple(t.strip().lower() for t in draft.event_tags if t.strip()),
            require_volatility=draft.require_volatility,
            require_trend=draft.require_trend,
            group_weights=weights,
            context_weight=draft.context_weight,
            direction_hint=draft.direction_hint,
            interpretation=draft.interpretation,
            parsed_by=parsed_by,
            notes=notes,
        )

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "timeframe": self.timeframe,
            "horizon": self.horizon,
            "horizonHours": self.horizon_hours,
            "horizonText": self.horizon_text,
            "eventKinds": list(self.event_kinds),
            "eventTags": list(self.event_tags),
            "requireVolatility": self.require_volatility,
            "requireVolatilityLabel": REGIME_VOLATILITY.get(self.require_volatility),
            "requireTrend": self.require_trend,
            "requireTrendLabel": REGIME_TREND.get(self.require_trend),
            "emphasis": sorted(self.group_weights),
            "contextWeight": self.context_weight,
            "directionHint": self.direction_hint,
            "interpretation": self.interpretation,
            "parsedBy": self.parsed_by,
            "notes": self.notes,
        }
