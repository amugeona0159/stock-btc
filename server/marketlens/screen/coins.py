"""코인을 원화로 읽는 표.

추천은 **원화로 낸다.** 이 프로그램을 쓰는 사람은 업비트에서 원화로 사고, `SOLUSDT`
라는 이름과 달러 가격은 그 사람이 실제로 치를 값이 아니다.

**환율로 환산하지 않는다.** 바이낸스 USDT 가격에 환율을 곱하면 숫자는 나오지만
**그 값에 살 수가 없다** — 국내 시세는 김치 프리미엄만큼 따로 논다. 투자 판단에 쓰는
화면에서 못 사는 가격을 적는 건 틀린 것보다 나쁘다. 그래서 **업비트 원화 마켓의 실제
가격**을 가져다 쓴다.

대신 **원화 마켓이 없는 코인은 추천에서 뺀다**(`NO_KRW`). 원화로 못 사는 것을
"오늘 살 만한 것" 목록에 올릴 수는 없다. 지수를 매수 후보에서 빼는 것과 같은 이치이고,
같은 방식으로 **학습에서는 그대로 쓴다** — 표를 쪼개지 않는다.
"""
from __future__ import annotations

# 티커 → 한글 이름. 티커만 있으면 외우고 있는 사람만 읽을 수 있다.
#
# 이름은 거래소 표기를 따른다(업비트 기준). 여기 없는 코인은 티커만 나가는데,
# 그게 맞다 — 없는 이름을 지어내면 다른 코인과 헷갈린다.
NAMES: dict[str, str] = {
    "BTC": "비트코인",
    "ETH": "이더리움",
    "SOL": "솔라나",
    "XRP": "리플",
    "DOGE": "도지코인",
    "ADA": "에이다",
    "AVAX": "아발란체",
    "LINK": "체인링크",
    "DOT": "폴카닷",
    "TRX": "트론",
    "BNB": "비앤비",
    "LTC": "라이트코인",
}

# 업비트에 원화 마켓이 없는 코인. **바이낸스 유니버스 기준으로 확인한 것이다**
# (2026-08, `/api/symbols?provider=upbit` 의 288개 원화 마켓과 대조).
#
# 업비트가 나중에 상장하면 여기서 지우면 된다. 자동으로 알아내지 않는 이유는,
# 추천을 얼리는 시각에 목록 조회가 실패하면 그날 종목이 통째로 바뀌기 때문이다 —
# 조용히 달라지는 것보다 손으로 고치는 편이 낫다.
NO_KRW: frozenset[str] = frozenset({"BNB", "LTC"})

# 이 접미사로 끝나면 코인 심볼로 본다. 순서가 중요하다 — `BTC` 를 먼저 두면
# `WBTCBTC` 같은 것에서 엉뚱하게 잘린다. 긴 것부터 본다.
_QUOTES: tuple[str, ...] = ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH")


def base(symbol: str) -> str:
    """거래쌍에서 코인만 떼어낸다. `SOLUSDT` → `SOL`, `KRW-SOL` → `SOL`.

    코인이 아니면 받은 것을 그대로 돌려준다 — 주식 심볼이 섞여 들어와도
    조용히 망가지지 않게.
    """
    if symbol.startswith("KRW-"):
        return symbol[4:]
    for quote in _QUOTES:
        # `BTCUSDT` 는 잘리지만 `BTC` 자체는 안 잘린다. 남는 게 없으면 쌍이 아니다.
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)]
    return symbol


def krw_market(symbol: str) -> str | None:
    """이 코인을 원화로 사는 업비트 마켓. 없으면 `None`."""
    coin = base(symbol)
    return None if coin in NO_KRW else f"KRW-{coin}"


def name(symbol: str) -> str | None:
    """한글 이름. 표에 없으면 `None` — 지어내지 않는다."""
    return NAMES.get(base(symbol))


def sellable_in_krw(symbol: str) -> bool:
    """원화로 살 수 있나. 추천 후보를 거르는 데 쓴다."""
    return base(symbol) not in NO_KRW
