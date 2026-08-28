"""종목 목록과 검색.

여기서 지키는 것 셋:

1. **토스 응답 파싱** — `result` 가 리스트다. 예전 코드는 `result["stocks"]` 를 찾다가
   `list.get` 으로 늘 터졌고, 그래서 토스 종목 검색이 한 번도 동작한 적이 없다.
2. **목록 캐시가 한 번만 나간다** — 통합 검색이 모든 시장을 동시에 때리므로, 여기가
   새면 토스가 429 로 막힌다.
3. **못 주는 시장은 오류가 아니다** — 야후는 전체 목록이 없다. 그건 상태지 고장이 아니다.
"""
from __future__ import annotations

import asyncio

import pytest

from marketlens.providers import base


# --- 순위 --------------------------------------------------------------

def _items(*pairs):
    return [base.item(symbol, name) for symbol, name in pairs]


def test_exact_symbol_wins():
    found = base.match(_items(("WBTCUSDT", "Wrapped"), ("BTC", "Bitcoin")), "BTC")
    assert found[0]["symbol"] == "BTC"


def test_a_token_after_the_separator_counts_as_exact():
    """`KRW-BTC` 의 `BTC` 를 완전일치로 쳐야 업비트에서 비트코인이 나온다.

    안 그러면 `KRW-BTC` 는 부분일치(3등급)로 밀려, 이름만 스친 종목들 뒤로 간다.
    실제로 "BTC" 를 치면 웜홀·쓰레스홀드가 먼저 나왔다.

    동률(둘 다 `BTC` 토큰)은 목록 순서가 가른다 — 업비트 목록은 KRW 마켓을 먼저
    담으므로 실제 화면에서는 `KRW-BTC` 가 위에 온다.
    """
    rows = _items(("XRPUSDT", "비트 비슷한 이름"), ("KRW-BTC", "비트코인"),
                  ("BTC-WOM", "웜홀"))
    order = [r["symbol"] for r in base.match(rows, "BTC")]
    # 토큰 완전일치 둘이 먼저, 이름만 스친 것은 뒤로.
    assert set(order[:2]) == {"KRW-BTC", "BTC-WOM"}
    assert base.match(base.prefer(rows, ("KRW-BTC",)), "BTC")[0]["symbol"] == "KRW-BTC"


def test_ties_keep_the_catalog_order():
    """목록이 담아 온 순서에 그 시장의 우선순위가 들어 있다(USDT 먼저, KRW 먼저).
    길이로 다시 줄 세우면 `BTC` 에 `BTCUSDT` 대신 `BTCTRY` 가 올라온다."""
    rows = _items(("BTCUSDT", "a"), ("BTCU", "b"), ("BTCTRY", "c"))
    assert [r["symbol"] for r in base.match(rows, "BTC")][0] == "BTCUSDT"


def test_korean_names_are_matched_without_case_folding():
    rows = _items(("005930", "삼성전자"), ("000810", "삼성화재"))
    assert len(base.match(rows, "삼성")) == 2


def test_prefer_moves_tracked_symbols_to_the_front():
    """한글 종목명에는 우선순위 실마리가 없다 — '삼성'에 삼성화재가 먼저 나왔다."""
    rows = _items(("000810", "삼성화재"), ("005930", "삼성전자"))
    ordered = base.prefer(rows, ("005930",))
    assert ordered[0]["symbol"] == "005930"
    assert base.match(ordered, "삼성")[0]["name"] == "삼성전자"


def test_item_builds_one_label_shape():
    assert base.item("005930", "삼성전자")["label"] == "삼성전자 (005930)"
    assert base.item("BTCUSDT")["label"] == "BTCUSDT"      # 이름이 없으면 심볼만


# --- 목록 캐시 ---------------------------------------------------------

def test_catalog_fetches_once_under_concurrency():
    """동시에 열 번 물어도 거래소에는 한 번만. 토스 429 를 막는 핵심 성질이다."""
    calls = []

    async def build():
        calls.append(1)
        await asyncio.sleep(0.05)
        return _items(("A", "가"))

    async def go():
        catalog = base.SymbolCatalog()
        await asyncio.gather(*(catalog.get(build) for _ in range(10)))

    asyncio.run(go())
    assert len(calls) == 1


def test_a_cancelled_caller_does_not_kill_the_fetch():
    """통합 검색이 시간 초과로 취소돼도 적재는 끝까지 간다.

    안 그러면 토스처럼 첫 적재가 오래 걸리는 시장은 몇 번을 쳐도 영원히 차갑다.
    """
    calls = []

    async def build():
        calls.append(1)
        await asyncio.sleep(0.2)
        return _items(("A", "가"))

    async def go():
        catalog = base.SymbolCatalog()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(catalog.get(build), timeout=0.05)
        await asyncio.sleep(0.3)                 # 적재가 끝날 시간
        items, stale = await catalog.get(build)
        return items, stale

    items, stale = asyncio.run(go())
    assert len(calls) == 1, "취소가 적재까지 죽였다"
    assert items and not stale


def test_stale_is_served_when_the_refresh_fails():
    """429 때문에 화면이 통째로 비는 것보다 하루 지난 목록이 낫다."""
    state = {"fail": False}

    async def build():
        if state["fail"]:
            raise base.ProviderError("429")
        return _items(("A", "가"))

    async def go():
        catalog = base.SymbolCatalog(ttl=0.01)
        await catalog.get(build)
        await asyncio.sleep(0.05)                # TTL 만료
        state["fail"] = True
        return await catalog.get(build)

    items, stale = asyncio.run(go())
    assert items and stale is True


def test_a_first_failure_still_raises():
    """가진 게 아무것도 없으면 조용히 빈 목록을 주지 않는다 — 그건 '없다'는 거짓말이다."""
    async def build():
        raise base.ProviderError("죽었다")

    async def go():
        with pytest.raises(base.ProviderError):
            await base.SymbolCatalog().get(build)

    asyncio.run(go())


# --- 토스 파싱 ---------------------------------------------------------

RESULT_LIST = {"result": [
    {"symbol": "005930", "name": "삼성전자", "securityType": "STOCK"},
    {"symbol": "000660", "name": "SK하이닉스", "securityType": "STOCK"},
]}
RESULT_NESTED = {"result": {"stocks": [
    {"symbol": "005930", "koreanName": "삼성전자", "securityType": "STOCK"},
]}}


@pytest.mark.parametrize("body, expected", [(RESULT_LIST, 4), (RESULT_NESTED, 2)])
def test_toss_catalog_reads_both_response_shapes(monkeypatch, body, expected):
    """실제 토스는 `{"result": [...]}` 를 준다. 예전 코드는 `result["stocks"]` 를
    찾다가 `list.get` 으로 터졌다 — 그래서 토스 검색이 늘 실패했다.

    두 모양을 다 받는다. 기대 건수가 시장 수(KOSPI+KOSDAQ)만큼 곱해진다.
    """
    from marketlens.providers import toss

    async def fake_get(path, params=None):
        return body

    monkeypatch.setattr(toss.client, "get", fake_get)
    provider = toss.TossProvider(
        toss.ProviderInfo(key="t", name="t", market="kr", timeframes=("1d",),
                          lists_symbols=True),
        stream_type="trade:kr",
    )
    found = asyncio.run(provider.catalog())
    assert len(found) == expected
    assert found[0]["name"] == "삼성전자"
    assert found[0]["market"] in ("KOSPI", "KOSDAQ")
    assert found[0]["kind"] == "STOCK"


def test_toss_catalog_raises_when_every_market_fails(monkeypatch):
    from marketlens.providers import toss

    async def fake_get(path, params=None):
        raise toss.ProviderError("429")

    monkeypatch.setattr(toss.client, "get", fake_get)
    provider = toss.TossProvider(
        toss.ProviderInfo(key="t", name="t", market="kr", timeframes=("1d",)),
        stream_type="trade:kr",
    )
    with pytest.raises(toss.ProviderError):
        asyncio.run(provider.catalog())


# --- 계약 --------------------------------------------------------------

def test_search_falls_back_to_the_catalog():
    """목록이 있는 프로바이더는 검색 코드를 따로 안 쓴다. 두 벌이면 목록 화면과
    검색 결과가 서로 다른 종목을 보여주게 된다."""
    class Listed(base.Provider):
        info = base.ProviderInfo(key="x", name="x", market="crypto",
                                 timeframes=("1d",), lists_symbols=True)

        async def history(self, symbol, timeframe, limit=500):
            raise NotImplementedError

        async def catalog(self):
            return _items(("AAA", "가"), ("BBB", "나"))

    found = asyncio.run(Listed().search("BBB"))
    assert [r["symbol"] for r in found] == ["BBB"]


def test_markets_without_a_catalog_say_so():
    """야후·KIS 는 전체 목록이 없다. 오류가 아니라 상태다."""
    from marketlens.providers import get

    for key in ("yahoo", "us_stock", "kis"):
        provider = get(key)
        assert provider.info.lists_symbols is False, key
        assert asyncio.run(provider.catalog()) == []


def test_markets_with_a_catalog_are_marked():
    from marketlens.providers import get

    for key in ("binance", "upbit", "toss_kr", "toss_us"):
        assert get(key).info.lists_symbols is True, key


def test_describe_tells_the_screen_which_markets_list():
    """화면이 목록을 부르기 전에 '검색만'을 표시할 수 있어야 한다."""
    from marketlens.providers import describe

    rows = {row["key"]: row for row in describe()}
    assert rows["binance"]["listsSymbols"] is True
    assert rows["yahoo"]["listsSymbols"] is False


def test_yahoo_refuses_korean_before_calling(monkeypatch):
    """야후는 한글 질의를 400 으로 거절한다. 보내 봐야 오류 문자열만 남는다."""
    from marketlens.providers import get

    with pytest.raises(base.ProviderUnavailable) as caught:
        asyncio.run(get("yahoo").search("삼성전자"))
    assert "한글" in str(caught.value)


# --- 통합 검색 ---------------------------------------------------------

def test_union_search_folds_the_same_symbol_in_one_market():
    """AAPL 이 야후·미국주식·토스미국 셋에서 온다. 세 줄이면 더 헷갈린다."""
    from marketlens.api import search as layer

    groups = [
        {"provider": "yahoo", "name": "야후", "market": "us",
         "items": [base.item("AAPL", "Apple")]},
        {"provider": "us_stock", "name": "미국주식", "market": "us",
         "items": [base.item("AAPL", "Apple")]},
    ]
    folded = layer._fold(groups)
    assert len(folded) == 1
    assert folded[0]["provider"] == "yahoo"
    assert folded[0]["items"][0]["also"] == ["us_stock"]


def test_union_search_keeps_different_markets_apart():
    """`BTCUSDT` 와 `KRW-BTC` 는 심볼이 달라 안 접힌다. 의도한 대로다."""
    from marketlens.api import search as layer

    groups = [
        {"provider": "binance", "name": "Binance", "market": "crypto",
         "items": [base.item("BTCUSDT", "BTC/USDT")]},
        {"provider": "upbit", "name": "Upbit", "market": "crypto",
         "items": [base.item("KRW-BTC", "비트코인")]},
    ]
    assert len(layer._fold(groups)) == 2
