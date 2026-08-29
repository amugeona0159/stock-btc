"""화면에 쓸 종목 이름."""
from __future__ import annotations

import pytest

from marketlens.screen import names, universe


@pytest.mark.parametrize("symbol, want", [
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("AAPL", "애플"),
    ("NVDA", "엔비디아"),
    ("SOLUSDT", "솔라나"),
    ("KRW-SOL", "솔라나"),        # 같은 코인은 어느 시장에서 와도 같은 이름
    ("^GSPC", "S&P 500"),
])
def test_known_symbols_read_in_korean(symbol, want):
    assert names.of(symbol) == want


def test_unknown_symbols_get_no_name():
    """**지어내지 않는다.** 없는 이름을 붙이면 다른 종목과 헷갈린다."""
    for symbol in ("ZZZZ", "999999", "NOSUCHUSDT"):
        assert names.of(symbol) is None


def test_every_recommendable_symbol_has_a_name():
    """추천에 오를 수 있는 종목은 전부 이름이 있어야 한다.

    유니버스를 늘리면서 표를 안 늘리면, 추천 목록에 티커만 덩그러니 뜬다.
    화면을 열어 봐야 알게 되는 종류라 여기서 잡는다.
    """
    missing: list[str] = []
    for provider in universe.providers():
        for symbol in universe.buyable(provider):
            if names.of(symbol) is None:
                missing.append(f"{provider}:{symbol}")
    assert not missing, f"이름이 없다 — names.py 에 더할 것: {missing}"


def test_ticker_keeps_what_people_search_with():
    """코인은 거래쌍을 떼고, 주식은 그대로. 국내주식 6자리 코드도 그대로 둔다 —
    그게 사람들이 검색창에 넣는 값이다."""
    assert names.ticker("SOLUSDT") == "SOL"
    assert names.ticker("KRW-SOL") == "SOL"
    assert names.ticker("AAPL") == "AAPL"
    assert names.ticker("005930") == "005930"


def test_a_stock_ticker_is_never_mistaken_for_a_trading_pair():
    """**주식 표를 먼저 본다.** 안 그러면 거래쌍 자르기가 주식 심볼을 갉는다.

    실제 위험이 있다 — `AVGO` 는 안전하지만, 유니버스에 `XXXETH` 같은 심볼이
    들어오면 코인 규칙이 `XXX` 로 잘라 버린다.
    """
    for symbol in list(names.US_STOCKS) + list(names.KR_STOCKS):
        assert names.ticker(symbol) == symbol, symbol


def test_no_symbol_is_named_twice_with_different_names():
    """표 셋이 겹치면 어느 쪽이 이기는지가 사전 순서에 달리게 된다."""
    seen: dict[str, str] = {}
    for table in (names.KR_STOCKS, names.US_STOCKS, names.INDICES, names.COINS):
        for symbol, label in table.items():
            assert seen.get(symbol, label) == label, symbol
            seen[symbol] = label


def test_names_are_not_blank():
    for table in (names.KR_STOCKS, names.US_STOCKS, names.INDICES, names.COINS):
        for symbol, label in table.items():
            assert label and label.strip() == label, symbol
