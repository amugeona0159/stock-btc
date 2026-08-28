"""프로바이더 계약.

구현이 넷이라 각자 테스트를 쓰면 계약이 넷으로 갈라진다. 검사는 한 벌이고
모든 구현이 그걸 통과한다.
"""
from __future__ import annotations

import pandas as pd
import pytest

from marketlens.core.candle import COLUMNS, validate
from marketlens.providers import base, describe, get
from marketlens.providers.base import CandleAggregator
from marketlens.providers.binance import BinanceProvider
from marketlens.providers.kis import KisProvider
from marketlens.providers.yahoo import YahooProvider
from marketlens.providers.upbit import UpbitProvider, _parse_utc
from tests.conftest import make_candles


# --- 계약 한 벌 -------------------------------------------------------------

def assert_contract(df: pd.DataFrame, timeframe: str) -> None:
    assert list(df.columns) == list(COLUMNS)
    assert validate(df, timeframe) == [], validate(df, timeframe)


class FakeProvider(base.Provider):
    """계약 검사가 실제로 무언가를 잡는지 확인하는 대조군."""

    info = base.ProviderInfo(key="fake", name="가짜", market="test", timeframes=("1h",))

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    async def history(self, symbol, timeframe, limit=500):
        return self._df


async def test_contract_passes_for_a_clean_frame():
    provider = FakeProvider(make_candles(count=50))
    assert_contract(await provider.history("X", "1h"), "1h")


async def test_contract_catches_out_of_order_bars():
    broken = make_candles(count=50)
    broken.loc[10, "ts"] = broken.loc[0, "ts"]
    with pytest.raises(AssertionError):
        assert_contract(broken, "1h")


async def test_csv_provider_round_trips(tmp_path, monkeypatch):
    frame = make_candles(count=80, timeframe="1h")
    csv_path = tmp_path / "TEST_1h.csv"
    frame[["ts", "open", "high", "low", "close", "volume"]].to_csv(csv_path, index=False)

    import marketlens.providers.csv_file as csv_module
    monkeypatch.setattr(csv_module, "DATA_DIR", tmp_path)

    provider = csv_module.CsvProvider()
    loaded = await provider.history("TEST", "1h")
    assert_contract(loaded, "1h")
    assert len(loaded) == 80
    assert loaded["close"].iloc[-1] == pytest.approx(frame["close"].iloc[-1])


async def test_csv_provider_rejects_path_escape(tmp_path, monkeypatch):
    """심볼은 사용자 입력이다. 경로로 새어 나가면 안 된다."""
    import marketlens.providers.csv_file as csv_module
    monkeypatch.setattr(csv_module, "DATA_DIR", tmp_path)
    with pytest.raises(base.ProviderError):
        await csv_module.CsvProvider().history("../../secrets", "1h")


# --- 프로바이더별 순수 변환 --------------------------------------------------

def test_binance_row_marks_the_forming_bar():
    step = 60_000
    now = 1_700_000_120_000
    kline = [1_700_000_060_000, "10", "12", "9", "11", "100"]
    row = BinanceProvider._row(kline, step, now)
    assert row["ts"] == 1_700_000_060_000
    assert row["closed"] is True

    forming = BinanceProvider._row([1_700_000_120_000, "10", "12", "9", "11", "100"], step, now)
    assert forming["closed"] is False


def test_upbit_parses_utc_without_a_timezone_marker():
    """Upbit 의 candle_date_time_utc 에는 Z 가 없다. 로컬로 읽으면 9시간 어긋난다."""
    assert _parse_utc("2026-08-28T00:00:00") == 1_787_875_200_000


def test_upbit_row_normalizes_field_names():
    row = UpbitProvider._row({
        "candle_date_time_utc": "2026-08-28T00:00:00",
        "opening_price": 100.0, "high_price": 110.0,
        "low_price": 95.0, "trade_price": 105.0,
        "candle_acc_trade_volume": 12.5,
    }, 3_600_000, 1_787_965_300_000)
    assert row["open"] == 100.0 and row["close"] == 105.0
    assert row["volume"] == 12.5
    assert row["closed"] is True


def test_yahoo_refuses_unknown_timeframe():
    provider = YahooProvider()
    assert provider.supports("1d") and provider.supports("5m")
    assert not provider.supports("3m")


def test_yahoo_is_history_only():
    """실시간이 없다고 표시돼야 화면이 '실시간 없음' 을 안내한다."""
    assert YahooProvider().info.realtime is False


def test_kis_is_unavailable_without_keys(monkeypatch):
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)
    provider = KisProvider()
    assert not provider.available
    assert "KIS_APP_KEY" in provider.unavailable_reason
    with pytest.raises(base.ProviderUnavailable):
        provider.check()


def test_registry_lists_only_usable_providers():
    keys = [p["key"] for p in describe()]
    assert "binance" in keys and "us_stock" in keys
    # 반쪽짜리는 목록에 없다 — 혼자서는 차트가 안 나온다.
    assert "finnhub" not in keys and "stooq" not in keys
    assert get("binance").info.market == "crypto"


# --- 틱을 봉으로 접기 --------------------------------------------------------

def test_aggregator_builds_one_bar_from_ticks():
    agg = CandleAggregator("1m")
    agg.add(1_700_000_000_000, 100.0, 1.0)
    agg.add(1_700_000_010_000, 105.0, 2.0)
    out = agg.add(1_700_000_020_000, 95.0, 3.0)
    candle = out[-1]
    assert candle.open == 100.0 and candle.high == 105.0
    assert candle.low == 95.0 and candle.close == 95.0
    assert candle.volume == 6.0
    assert candle.closed is False


def test_aggregator_closes_the_previous_bar_first():
    """확정 봉이 새 봉보다 먼저 나가야 화면이 순서대로 처리한다."""
    agg = CandleAggregator("1m")
    agg.add(1_700_000_000_000, 100.0, 1.0)
    out = agg.add(1_700_000_060_000, 110.0, 2.0)
    assert len(out) == 2
    assert out[0].closed is True and out[0].close == 100.0
    assert out[1].closed is False and out[1].open == 110.0


def test_aggregator_drops_late_ticks():
    """이미 닫은 봉을 되살리지 않는다. 되살리면 확정된 시그널이 뒤집힌다."""
    agg = CandleAggregator("1m")
    agg.add(1_700_000_060_000, 110.0, 1.0)
    assert agg.add(1_700_000_000_000, 999.0, 1.0) == []


# --- 토스증권 --------------------------------------------------------------

def test_toss_is_unavailable_without_keys(monkeypatch):
    from marketlens.providers import toss

    monkeypatch.delenv("TOSS_CLIENT_ID", raising=False)
    monkeypatch.delenv("TOSS_CLIENT_SECRET", raising=False)
    provider = base.get("toss_kr")
    assert not provider.available
    assert "TOSS_CLIENT_ID" in provider.unavailable_reason
    assert not toss.client.configured


def test_toss_registers_kr_and_us_separately():
    """사건 범위(market:kr / market:us)가 갈려야 이벤트 스터디가 섞이지 않는다."""
    assert base.get("toss_kr").info.market == "kr"
    assert base.get("toss_us").info.market == "us"


def test_toss_refuses_timeframes_it_cannot_serve():
    """토스는 1m 과 1d 만 준다. 1h 를 받아 주면 화면이 빈 차트를 본다."""
    provider = base.get("toss_kr")
    assert provider.supports("1m") and provider.supports("1d") and provider.supports("1w")
    assert not provider.supports("1h")
    with pytest.raises(base.ProviderError):
        provider._native("1h")


def test_toss_row_parses_strings_and_offsets():
    """가격이 문자열로, 시각이 오프셋 붙은 ISO 로 온다. 그 변환이 여기서 끝나야 한다."""
    from marketlens.providers.toss import TossProvider

    row = TossProvider._row({
        "timestamp": "2026-03-25T09:30:00.000+09:00",
        "openPrice": "72000", "highPrice": "72500",
        "lowPrice": "71800", "closePrice": "72300", "volume": "1200",
    }, step=60_000, now=1_800_000_000_000)
    # +09:00 09:30 = UTC 00:30
    assert pd.Timestamp(row["ts"], unit="ms", tz="UTC").hour == 0
    assert row["open"] == 72000.0 and row["close"] == 72300.0
    assert row["volume"] == 1200.0 and row["closed"] is True


def test_toss_marks_the_forming_bar():
    from marketlens.providers.toss import TossProvider

    stamp = "2026-03-25T09:30:00.000+09:00"
    ts = int(pd.Timestamp(stamp).tz_convert("UTC").timestamp() * 1000)
    forming = TossProvider._row({
        "timestamp": stamp, "openPrice": "1", "highPrice": "1",
        "lowPrice": "1", "closePrice": "1", "volume": "0",
    }, step=86_400_000, now=ts + 1000)
    assert forming["closed"] is False
