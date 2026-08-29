"""닫은 판의 성적표.

## 통화를 더하지 않는다

원화 판과 달러 판을 한 숫자로 더하면 그건 아무것도 아닌 값이 된다. 환율로 맞추지도
않는다 — `screen/coins.py` 가 적어 둔 것과 같은 이유다. 그래서 성적은 **통화마다**
따로 난다. 판수와 승률만 전체로 낸다(그건 통화를 안 타니까).

## 수익률은 원금 대비다

`실현손익 / (진입가 × 판 주수)`. 남은 주수로 나누면 부분 익절한 판이 과대평가된다 —
절반을 팔고 절반이 남았는데 원금을 절반으로 잡으면 수익률이 두 배가 된다.

## 누가 닫았는지 적는다

손절로 닫힌 판과 손으로 닫은 판은 다르게 읽어야 한다. 손으로 닫는 건 계획 밖의
행동이고, 그게 많으면 계획이 아니라 기분으로 파는 중이라는 뜻이다.
"""
from __future__ import annotations

from collections import defaultdict

from .store import CLOSED, Position


def _profit_pct(position: Position) -> float | None:
    base = position.entry * position.shares
    if base <= 0:
        return None
    return position.realized / base * 100.0


def one(position: Position) -> dict:
    """판 하나의 성적. 아직 안 닫힌 판도 실현분까지는 잰다."""
    return {
        "id": position.id,
        "symbol": position.symbol,
        "currency": position.currency,
        "openedAt": position.opened_at,
        "closedAt": position.closed_at,
        "reason": position.close_reason,
        "source": position.source,
        "entry": position.entry,
        "shares": position.shares,
        "realized": round(position.realized, 4),
        "profitPct": round(v, 3) if (v := _profit_pct(position)) is not None else None,
    }


def summary(positions: list[Position]) -> dict:
    """닫은 판들의 성적표."""
    closed = [p for p in positions if p.status == CLOSED]
    if not closed:
        return {"n": 0, "byCurrency": {}, "reasons": {}}

    per: dict[str, dict] = defaultdict(lambda: {"n": 0, "realized": 0.0, "pct": []})
    reasons: dict[str, int] = defaultdict(int)
    wins = 0
    for position in closed:
        bucket = per[position.currency]
        bucket["n"] += 1
        bucket["realized"] += position.realized
        got = _profit_pct(position)
        if got is not None:
            bucket["pct"].append(got)
        reasons[position.close_reason or "manual"] += 1
        if position.realized > 0:
            wins += 1

    by_currency = {
        currency: {
            "n": body["n"],
            "realized": round(body["realized"], 2),
            # 평균 수익률은 판마다 같은 무게로 본다. 원금이 큰 판에 눌리면
            # "고르는 실력"이 아니라 "얼마를 넣었나"를 재게 된다.
            "avgPct": round(sum(body["pct"]) / len(body["pct"]), 3) if body["pct"] else None,
        }
        for currency, body in per.items()
    }
    return {
        "n": len(closed),
        "wins": wins,
        "winRate": round(wins / len(closed), 3),
        "byCurrency": by_currency,
        "reasons": dict(reasons),
    }
