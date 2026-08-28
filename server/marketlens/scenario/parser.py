"""질문 → 시나리오.

두 단계다:
1. **규칙 파서** — 한국어/영어의 기간·사건 표현을 사전으로 잡는다. 키가 없어도 돌고,
   같은 질문이면 항상 같은 답이 나온다.
2. **Claude** — 키가 있으면 규칙 결과를 초안으로 주고 다듬게 한다.

규칙 파서를 지우지 말 것. LLM 이 없을 때의 폴백이자, LLM 이 이상한 값을 냈을 때의
비교 기준이다. 그리고 결과는 언제나 **폼으로 나가서 사람이 고칠 수 있다** — 그게
"자연어 + 구조화 폼" 의 뜻이다.
"""
from __future__ import annotations

import logging
import os
import re

from .schema import Scenario, ScenarioDraft

log = logging.getLogger("marketlens.scenario")

# 기간 표현 → 시간. 숫자가 붙은 표현은 아래 정규식이 따로 잡는다.
HORIZON_WORDS: list[tuple[tuple[str, ...], float, str]] = [
    (("오늘", "하루", "1일", "내일", "today", "tomorrow"), 24, "1일"),
    (("이틀", "2일"), 48, "2일"),
    (("사흘", "3일"), 72, "3일"),
    (("일주일", "1주", "한주", "한 주", "week", "주간"), 168, "1주일"),
    (("2주", "이주", "보름"), 336, "2주일"),
    (("한달", "한 달", "1개월", "month", "월간"), 720, "1개월"),
    (("분기", "3개월", "quarter"), 2160, "3개월"),
    (("반년", "6개월"), 4320, "6개월"),
    (("1년", "일년", "year"), 8760, "1년"),
]

_NUMBER_UNITS = {
    "시간": 1, "hour": 1, "h": 1,
    "일": 24, "day": 24, "d": 24,
    "주": 168, "week": 168, "w": 168,
    "개월": 720, "달": 720, "month": 720, "m": 720,
    "년": 8760, "year": 8760, "y": 8760,
}
_NUMBER_PATTERN = re.compile(
    r"(\d+)\s*(시간|hours?|일|days?|주일?|weeks?|개월|달|months?|년|years?)", re.I
)

# 사건 표현 → (종류, 태그들)
EVENT_WORDS: list[tuple[tuple[str, ...], str, tuple[str, ...]]] = [
    (("fomc", "금리", "연준", "fed", "기준금리", "rate"), "macro", ("rate", "monetary")),
    (("cpi", "물가", "인플레", "inflation"), "macro", ("inflation",)),
    (("고용", "실업", "employment", "jobs"), "macro", ("labor",)),
    (("규제", "sec", "정책", "법안", "regulation", "ban"), "regulation", ("regulation",)),
    (("etf", "승인", "상장"), "regulation", ("etf",)),
    (("반감기", "halving"), "crypto", ("halving", "supply")),
    (("해킹", "hack", "탈취", "익스플로잇"), "crypto", ("hack",)),
    (("파산", "청산", "bankrupt", "insolvency", "ftx"), "crypto", ("insolvency", "contagion")),
    (("실적", "어닝", "earnings"), "company", ("earnings",)),
    (("전쟁", "지정학", "war", "geopolitic"), "macro", ("geopolitics",)),
    (("급락", "폭락", "crash", "dump"), "chart", ("spike", "down")),
    (("급등", "폭등", "pump", "rally"), "chart", ("spike", "up")),
    (("거래량", "volume"), "chart", ("volume",)),
    (("신고가", "돌파", "breakout", "신고점"), "chart", ("breakout",)),
    (("만기", "expiry", "옵션"), "calendar", ("expiry",)),
    (("월말", "분기말", "리밸런싱"), "calendar", ("month-end", "quarter-end")),
]

VOLATILITY_WORDS = [
    (("고변동", "변동성이 클", "변동성 클", "출렁", "volatile"), 2),
    (("저변동", "조용", "잠잠", "quiet", "횡보장"), 0),
]
TREND_WORDS = [
    (("상승추세", "상승장", "불장", "bull", "오르는 중"), 1),
    (("하락추세", "하락장", "약세장", "bear", "내리는 중"), -1),
    (("횡보", "박스", "sideways", "range"), 0),
]
EMPHASIS_WORDS = [
    (("변동성", "volatility"), "volatility"),
    (("거래량", "volume"), "volume"),
    (("추세", "trend"), "trend"),
    (("모멘텀", "momentum", "rsi"), "momentum"),
    (("시간대", "요일", "계절", "분기", "월말", "calendar"), "calendar"),
]
DIRECTION_WORDS = [
    (("오를", "상승", "올라", "반등", "up", "rise"), 1),
    (("내릴", "하락", "떨어", "빠질", "down", "fall"), -1),
]


def parse_rules(question: str, timeframe: str) -> ScenarioDraft:
    """사전과 정규식만으로. 키가 없어도 여기까지는 항상 된다."""
    text = question.lower()
    draft = ScenarioDraft()
    reasons: list[str] = []

    # --- 기간 ---
    matched = _NUMBER_PATTERN.search(question)
    if matched:
        amount = int(matched.group(1))
        unit = matched.group(2).lower()
        hours = amount * next(
            (v for k, v in _NUMBER_UNITS.items() if unit.startswith(k)), 24
        )
        draft.horizon_hours = float(min(8760, max(1, hours)))
        draft.horizon_text = f"{amount}{matched.group(2)}"
        reasons.append(f"기간을 '{draft.horizon_text}' 로 읽었다")
    else:
        for words, hours, label in HORIZON_WORDS:
            if any(word in text for word in words):
                draft.horizon_hours, draft.horizon_text = float(hours), label
                reasons.append(f"기간을 '{label}' 로 읽었다")
                break

    # --- 사건 ---
    kinds: list[str] = []
    tags: list[str] = []
    for words, kind, event_tags in EVENT_WORDS:
        if any(word in text for word in words):
            if kind not in kinds:
                kinds.append(kind)
            tags.extend(t for t in event_tags if t not in tags)
    draft.event_kinds, draft.event_tags = kinds, tags
    if tags:
        reasons.append(f"사건 조건: {', '.join(tags)}")

    # --- 레짐 ---
    for words, value in VOLATILITY_WORDS:
        if any(word in text for word in words):
            draft.require_volatility = value
            reasons.append(f"변동성 조건: {value}")
            break
    for words, value in TREND_WORDS:
        if any(word in text for word in words):
            draft.require_trend = value
            reasons.append(f"추세 조건: {value}")
            break

    # --- 강조 축 ---
    draft.emphasis = [
        key for words, key in EMPHASIS_WORDS if any(word in text for word in words)
    ]

    # --- 방향 전제 ---
    for words, value in DIRECTION_WORDS:
        if any(word in text for word in words):
            draft.direction_hint = value
            break

    # 사건 조건이 붙으면 상황 쪽에 무게를 더 준다 — 모양만 맞는 남의 사건을 덜 가져오게.
    if kinds:
        draft.context_weight = 0.65

    draft.interpretation = " · ".join(reasons) if reasons else \
        f"특별한 조건 없이 {draft.horizon_text} 뒤를 본다"
    return draft


SYSTEM = """너는 주식·암호화폐 차트 분석 도구의 질문 해석기다.
사용자의 질문을 도구가 쓸 수 있는 조건으로만 바꾼다. **예측을 하지 마라** —
가격이 오를지 내릴지는 도구가 과거 데이터로 계산한다. 네 일은 질문을 조건으로 옮기는 것뿐이다.

규칙:
- 질문에 없는 조건을 만들어내지 마라. 사건 얘기가 없으면 event_kinds 와 event_tags 는 빈 목록이다.
- horizon_hours 는 질문의 기간을 시간으로 옮긴 것이다. 기간 언급이 없으면 168(1주일)로 둔다.
- direction_hint 는 질문이 방향을 전제할 때만 채운다. 그게 예측을 바꾸지는 않는다.
- interpretation 은 한국어 한 문장으로, 질문을 어떻게 읽었는지 쓴다. 사용자가 이걸 보고 고친다."""


async def parse_llm(question: str, timeframe: str, fallback: ScenarioDraft) -> ScenarioDraft | None:
    """Claude 로 다듬는다. 키가 없거나 실패하면 None — 호출부가 규칙 결과를 쓴다."""
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return None
    try:
        import anthropic
    except ImportError:
        log.info("anthropic 패키지가 없어 규칙 파서만 쓴다")
        return None

    client = anthropic.AsyncAnthropic()
    prompt = (
        f"질문: {question}\n"
        f"차트 봉 단위: {timeframe}\n\n"
        f"규칙 기반 파서의 초안(참고용, 틀릴 수 있다):\n{fallback.model_dump_json()}\n\n"
        "이 질문을 조건으로 옮겨라."
    )
    try:
        response = await client.messages.parse(
            model="claude-opus-5",
            max_tokens=2000,
            system=SYSTEM,
            # 질문 한 줄을 폼으로 옮기는 일이다. 깊게 생각할 게 없으니 낮은 노력으로 둔다.
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}],
            output_format=ScenarioDraft,
        )
        return response.parsed_output
    except Exception as exc:  # noqa: BLE001 - 해석 실패로 화면이 죽으면 안 된다
        log.info("Claude 질문 해석 실패, 규칙 결과를 쓴다: %s", exc)
        return None


async def parse(question: str, timeframe: str, use_llm: bool = True) -> Scenario:
    draft = parse_rules(question, timeframe)
    parsed_by = "rule"
    if use_llm:
        refined = await parse_llm(question, timeframe, draft)
        if refined is not None:
            draft, parsed_by = refined, "llm"
    return Scenario.from_draft(draft, question, timeframe, parsed_by)


def from_form(payload: dict, question: str, timeframe: str) -> Scenario:
    """폼에서 그대로 온 것. 사람이 고친 값이 최종이다."""
    draft = ScenarioDraft(**payload)
    return Scenario.from_draft(draft, question, timeframe, "form")
