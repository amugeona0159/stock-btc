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


# ------------------------------------------------- 화면에 넘기는 표 (`/api/names`)

def test_table_is_keyed_by_the_symbol_itself():
    """**열쇠가 `SOL` 이 아니라 `SOLUSDT` 다.**

    코인 기호로만 주면 화면이 거래쌍을 떼는 규칙(`coins.base`)을 TypeScript 로
    한 벌 더 갖게 되고, 그 순간 두 벌이 갈라진다.
    """
    table = names.table()
    assert table["SOLUSDT"] == {"name": "솔라나", "ticker": "SOL"}
    assert table["KRW-SOL"] == {"name": "솔라나", "ticker": "SOL"}
    assert table["005930"] == {"name": "삼성전자", "ticker": "005930"}


def test_table_covers_everything_the_screens_can_show():
    """추천·변동 순위·헤더가 보여줄 수 있는 종목은 전부 표에 있어야 한다.

    빠지면 그 종목만 티커로 뜨는데, 화면을 열어 봐야 아는 종류다.
    """
    table = names.table()
    for provider in universe.providers():
        for symbol in universe.symbols(provider):
            assert symbol in table, f"{provider}:{symbol}"


def test_table_leaves_out_what_it_does_not_know():
    """거래소 목록을 통째로 싣지 않는다. 바이낸스만 1,358종이다."""
    table = names.table()
    assert "PEPEUSDT" not in table
    assert len(table) < 100


def test_the_same_coin_reads_the_same_in_every_market():
    """`XRPUSDT` 와 `KRW-XRP` 가 다른 이름으로 뜨면 안 된다.

    **실제로 그랬다** — 손으로 적은 `리플` 과 업비트의 `엑스알피(리플)` 이
    추천 목록과 종목 고르기 화면에서 갈렸다.
    """
    table = names.table()
    for coin, symbols in {
        "XRP": ("XRPUSDT", "KRW-XRP"),
        "SOL": ("SOLUSDT", "KRW-SOL"),
        "BTC": ("BTCUSDT", "KRW-BTC"),
    }.items():
        found = {table[s]["name"] for s in symbols if s in table}
        assert len(found) == 1, f"{coin}: {found}"


def test_coin_names_follow_upbit():
    """코인 이름의 출처는 **업비트**다(원화로 사는 곳이니까).

    업비트를 실제로 부르지는 않는다 — 테스트가 네트워크를 타면 안 된다. 대신
    한 번 어긋났던 그 값을 고정해, 짧게 줄이려는 손을 막는다.
    확인 방법은 `names.py` 표에 적어 뒀다(`/api/symbols?provider=upbit`).
    """
    assert names.COINS["XRP"] == "엑스알피(리플)", "업비트 표기다. 줄이면 화면이 갈라진다"
