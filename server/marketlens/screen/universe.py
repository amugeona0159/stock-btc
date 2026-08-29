"""후보 종목 표 — **한 벌만.**

같은 목록이 두 곳에 있으면 갈라진다. 이 표는 두 군데서 쓴다:

- 추천(`screen`) — 오늘 볼 만한 종목을 이 안에서 고른다
- 학습(`routes.PEERS`) — 여러 종목을 모아 풀링 학습할 때의 동료

둘의 목적은 다르지만 내용은 같아도 된다. 축이 전부 무차원이라 종목이 늘수록
학습 표본만 커지고, 추천은 후보가 넓을수록 낫다.

**횡단면 순위를 매기는 표라 종목 수가 중요하다.** 다섯 개짜리 목록에서 상위 3개를
고르는 건 순위가 아니라 그냥 나열이다. 시장마다 최소 열 종목은 둔다.
"""
from __future__ import annotations

from . import coins

UNIVERSE: dict[str, tuple[str, ...]] = {
    # 시가총액 상위 위주. 유동성이 없으면 봉이 비어 팩터가 잡음이 된다.
    "binance": ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT",
                "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "LTCUSDT", "TRXUSDT"),
    "upbit": ("KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE",
              "KRW-ADA", "KRW-AVAX", "KRW-LINK", "KRW-DOT", "KRW-TRX"),
    # 지수(^GSPC·^IXIC)를 같이 둔다. 개별주만 있으면 "시장이 통째로 밀린 날"과
    # "이 종목만 밀린 날"을 구별할 수 없다.
    "yahoo": ("AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AVGO",
              "JPM", "XOM", "^GSPC", "^IXIC"),
    "us_stock": ("AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AVGO",
                 "JPM", "XOM"),
    "toss_us": ("AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AVGO",
                "JPM", "XOM"),
    # KRX 코드 6자리. 삼성전자·SK하이닉스·카카오·현대차·LG화학·NAVER·POSCO홀딩스·
    # 셀트리온·삼성바이오로직스·기아.
    "toss_kr": ("005930", "000660", "035720", "005380", "051910",
                "035420", "005490", "068270", "207940", "000270"),
    "kis": ("005930", "000660", "035720", "005380", "051910",
            "035420", "005490", "068270", "207940", "000270"),
}

# 횡단면 순위가 의미를 가지려면 같은 시각에 이만큼은 있어야 한다.
MIN_BREADTH = 5

# 매수 후보에서 빼는 것. 지수는 살 수 있는 물건이 아니다.
#
# **표를 쪼개지 않는다.** 지수는 학습 동료로는 그대로 쓴다 — "시장이 통째로 밀린 날"과
# "이 종목만 밀린 날"을 가르는 축이라 빼면 손해다. 여기서는 같은 표를 거른 **한 갈래**를
# 돌려줄 뿐이다.
#
# 접두사(`^`)로 거르지 않는다. 그건 프로바이더 일곱 곳의 표기 규칙에 대한 추측이다.
NOT_BUYABLE: frozenset[str] = frozenset({"^GSPC", "^IXIC"})


def symbols(provider: str) -> tuple[str, ...]:
    """적재·학습에 쓰는 전체 목록."""
    return UNIVERSE.get(provider, ())


def buyable(provider: str) -> tuple[str, ...]:
    """매수 후보. 살 수 없는 것은 뺀다 — 기준선(후보 평균)에서도 빠진다.

    코인은 **원화로 살 수 있는 것만** 남는다. 추천을 원화로 내기로 했으므로
    (`coins.py`), 원화 마켓이 없는 코인은 "오늘 살 만한 것" 에 올릴 수가 없다.
    지수를 빼는 것과 같은 이치이고, 학습에서는 `symbols()` 로 그대로 쓴다.
    """
    return tuple(s for s in symbols(provider)
                 if s not in NOT_BUYABLE and coins.sellable_in_krw(s))


def providers() -> tuple[str, ...]:
    return tuple(UNIVERSE)
