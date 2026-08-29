"""판이 닫힌 뒤 — 얼마나 쉬고, 언제 얼마에 다시 들어가나.

## 지어내지 않는다

"3일 쉬어라" 같은 말은 이 도구가 잰 무엇과도 이어져 있지 않다. 여기서 낼 수 있는 건
**근거가 어디서 끊겼고 다음 근거가 언제 오는가** 뿐이다.

- 한 판의 근거는 그날 아침 추천의 **N일 지평 예측**이다.
- 손절로 닫혔다는 건 80% 밴드의 아래끝을 뚫었다는 뜻이고, 그건 **그 예측이 빗나갔다**는
  말이다. 빗나간 예측으로 같은 자리에 다시 들어갈 근거는 없다.
- 목표까지 갔으면 그 예측은 **다 쓴 것**이다. 남은 근거가 없다.

두 경우 다 답은 같다 — **다음 아침 추천까지 근거가 없다.** 그래서 쉬는 기간을 날짜로
지어내는 대신 "다음 추천까지"라고 적는다. 그리고 오늘 추천에 그 종목이 이미 올라와
있으면, 그 밴드 아래끝이 곧 재진입 값이다(추천이 `buy_below` 규칙을 만들 때 쓰는 값과
같다 — 표를 두 벌로 만들지 않는다).

`CLAUDE.md` 의 "'언제 도달' 은 1·2·3일 지평으로만 쓴다" 와 같은 규칙이다. 모델이 아는
시간 단위 밖으로 나가는 순간 그건 예측이 아니라 말투다.
"""
from __future__ import annotations

from ..screen import names
from .store import Position

# 추천이 `buy_below` 를 만들 때 쓰는 값과 같다 — 밴드 아래끝.
def reentry_price(last: float | None, band: list[float] | None) -> float | None:
    if not last or not band or len(band) != 2:
        return None
    return last * (1 + band[0] / 100.0)


def _found(today: dict, symbol: str) -> dict | None:
    """오늘 추천(매수 쪽)에서 이 종목을 찾는다."""
    if not today or not today.get("available"):
        return None
    for row in today.get("buy") or []:
        if row.get("symbol") == symbol:
            return row
    return None


def after_close(position: Position, today: dict | None = None) -> dict:
    """닫힌 판 뒤에 할 일. `today` 는 `api/recommend.py: today()` 의 결과다."""
    reason = position.close_reason or "manual"
    horizon = position.days
    what = names.label(position.symbol)

    if reason == "stop":
        why = (f"{what} 은 손절선에서 닫혔다. 그 판의 근거였던 "
               f"{horizon or '단기'}일 예측이 80% 밴드 아래끝을 뚫었으니 **빗나간 것**이다. "
               "같은 값에 다시 들어갈 근거는 남아 있지 않다.")
    elif reason == "target":
        why = (f"{what} 은 목표까지 갔다. 그 판의 근거였던 "
               f"{horizon or '단기'}일 예측은 **다 쓴 것**이라 지금 값에 대한 말이 없다.")
    else:
        why = f"{what} 을 손으로 닫았다. 계획 밖이라 이 도구가 댈 근거가 없다."

    found = _found(today or {}, position.symbol)
    if found is None:
        # **오늘 추천에 없으면 없다고 한다.** 여기서 값을 만들어 내면 그게 제일 나쁘다.
        return {
            "reason": reason,
            "why": why,
            "rest": "다음 아침 추천까지",
            "restWhy": ("이 도구가 보는 시간은 1~3일이라 그보다 먼 계획은 못 낸다. "
                        "다음 근거는 내일 아침 추천에서 나온다."),
            "reentry": None,
            "note": "오늘 추천 목록에는 이 종목이 없다.",
        }

    price = reentry_price(found.get("last"), found.get("band"))
    return {
        "reason": reason,
        "why": why,
        "rest": "오늘 추천에 다시 올라 있다",
        "restWhy": ("쉬는 기간을 날짜로 셀 필요가 없다 — 새 근거가 이미 나왔다. "
                    "다만 이건 오늘 아침에 뽑은 값이고, 그날 안 바뀐다."),
        "reentry": None if price is None else {
            "price": round(price, 8),
            "last": found.get("last"),
            "band": found.get("band"),
            "expected": found.get("expected"),
            "days": (today or {}).get("days"),
            "how": "80% 밴드의 아래끝. 추천이 매수 알림을 걸 때 쓰는 값과 같다.",
        },
        "note": None,
    }
