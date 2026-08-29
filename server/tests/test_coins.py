"""코인을 원화로 읽는 표."""
from __future__ import annotations

import pytest

from marketlens.screen import coins, names, universe


@pytest.mark.parametrize("symbol, want", [
    ("SOLUSDT", "SOL"),
    ("BTCUSDT", "BTC"),
    ("KRW-SOL", "SOL"),
    ("KRW-BTC", "BTC"),
    ("ETHBTC", "ETH"),
    ("AAPL", "AAPL"),          # 주식은 그대로
    ("005930", "005930"),      # 국내주식 코드도 그대로
    ("^GSPC", "^GSPC"),
])
def test_base_strips_the_quote_currency(symbol, want):
    assert coins.base(symbol) == want


def test_base_does_not_eat_a_bare_ticker():
    """`BTC` 자체는 자르면 안 된다. 남는 게 없으면 거래쌍이 아니다."""
    assert coins.base("BTC") == "BTC"
    assert coins.base("ETH") == "ETH"


def test_base_prefers_the_longer_quote():
    """`USDT` 를 `USD` 보다 먼저 봐야 한다. 아니면 `SOLUSDT` 가 `SOLT` 가 된다."""
    assert coins.base("SOLUSDT") == "SOL"
    assert coins.base("SOLUSDC") == "SOL"


def test_krw_market_maps_to_upbit():
    assert coins.krw_market("SOLUSDT") == "KRW-SOL"
    assert coins.krw_market("KRW-SOL") == "KRW-SOL"


def test_coins_without_a_krw_market_have_none():
    """업비트에 원화 마켓이 없는 코인. 없는 마켓 이름을 만들어 내면 안 된다."""
    for symbol in ("BNBUSDT", "LTCUSDT"):
        assert coins.krw_market(symbol) is None
        assert not coins.sellable_in_krw(symbol)


def test_every_named_coin_has_a_market_or_is_marked():
    """이름은 있는데 원화로 못 사는 코인은 `NO_KRW` 에 적혀 있어야 한다.

    안 적혀 있으면 `KRW-XXX` 라는 없는 마켓을 조회하러 간다.
    """
    for ticker in names.COINS:
        market = coins.krw_market(ticker)
        assert (market is None) == (ticker in coins.NO_KRW), ticker


def test_recommendation_only_offers_what_you_can_buy_in_krw():
    """**추천은 원화로 낸다.** 원화로 못 사는 것을 '오늘 살 만한 것' 에 올릴 수 없다."""
    buyable = universe.buyable("binance")
    assert "BNBUSDT" not in buyable
    assert "LTCUSDT" not in buyable
    assert "SOLUSDT" in buyable
    for symbol in buyable:
        assert coins.krw_market(symbol) is not None, symbol


def test_training_still_sees_the_dropped_coins():
    """**표를 쪼개지 않는다.** 원화로 못 사도 학습 동료로는 그대로 쓴다 —
    빼면 횡단면이 좁아져서 순위 자체가 나빠진다."""
    everything = universe.symbols("binance")
    assert "BNBUSDT" in everything
    assert "LTCUSDT" in everything
    assert len(everything) > len(universe.buyable("binance"))


def test_dropping_coins_does_not_break_the_breadth_floor():
    """거르고도 횡단면 순위를 매길 만큼은 남아야 한다.

    다섯 개에서 상위 3개를 고르는 건 순위가 아니라 나열이다.
    """
    for provider in ("binance", "upbit"):
        assert len(universe.buyable(provider)) >= universe.MIN_BREADTH, provider


def test_stocks_are_untouched_by_the_krw_rule():
    """주식은 이 규칙과 무관하다. 코인 규칙이 주식 후보를 건드리면 안 된다."""
    for provider in ("yahoo", "toss_kr", "toss_us"):
        for symbol in universe.buyable(provider):
            assert coins.sellable_in_krw(symbol), symbol
