from __future__ import annotations

import pandas as pd
import pytest

from marketlens.core.candle import (COLUMNS, Candle, closed_only, resample,
                                    to_frame, upsert, validate)
from marketlens.core.timeframe import floor_ts, next_ts, to_ms
from tests.conftest import make_candles


def test_timeframe_parsing():
    assert to_ms("1m") == 60_000
    assert to_ms("4h") == 14_400_000
    assert to_ms("1w") == 604_800_000
    with pytest.raises(ValueError):
        to_ms("1y")
    with pytest.raises(ValueError):
        to_ms("0m")


def test_floor_and_next_align_to_grid():
    ts = 1_700_000_123_456
    assert floor_ts(ts, "1m") % 60_000 == 0
    assert next_ts(ts, "1m") - floor_ts(ts, "1m") == 60_000


def test_frame_has_fixed_column_order(candles):
    assert list(candles.columns) == list(COLUMNS)


def test_duplicate_timestamps_keep_the_latest():
    rows = [
        {"ts": 1000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10, "closed": True},
        {"ts": 1000, "open": 1, "high": 3, "low": 0.5, "close": 2.0, "volume": 20, "closed": True},
    ]
    df = to_frame(rows)
    assert len(df) == 1
    assert df["close"].iloc[0] == 2.0


def test_rows_are_sorted_by_time():
    rows = [
        {"ts": 2000, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "closed": True},
        {"ts": 1000, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "closed": True},
    ]
    assert to_frame(rows)["ts"].tolist() == [1000, 2000]


def test_upsert_replaces_the_last_bar(candles):
    last_ts = int(candles["ts"].iloc[-1])
    updated = upsert(candles, Candle(last_ts, 1, 9, 0.5, 7, 42, closed=False))
    assert len(updated) == len(candles)
    assert updated["close"].iloc[-1] == 7
    assert not bool(updated["closed"].iloc[-1])


def test_upsert_appends_a_new_bar(candles):
    step = int(candles["ts"].iloc[1] - candles["ts"].iloc[0])
    next_bar = int(candles["ts"].iloc[-1]) + step
    updated = upsert(candles, Candle(next_bar, 1, 2, 0.5, 1.5, 3, closed=False))
    assert len(updated) == len(candles) + 1
    assert int(updated["ts"].iloc[-1]) == next_bar


def test_closed_only_drops_the_forming_bar():
    df = make_candles(count=50, closed_tail=False)
    assert not bool(df["closed"].iloc[-1])
    assert len(closed_only(df)) == len(df) - 1
    assert closed_only(df)["closed"].all()


def test_validate_accepts_a_clean_frame(candles):
    assert validate(candles, "1h") == []


def test_validate_catches_broken_bars(candles):
    broken = candles.copy()
    broken.iloc[5, broken.columns.get_loc("high")] = 0.0   # high < low
    problems = validate(broken, "1h")
    assert any("high" in p for p in problems)


def test_validate_catches_an_unclosed_middle_bar(candles):
    broken = candles.copy()
    broken.iloc[5, broken.columns.get_loc("closed")] = False
    assert any("미확정" in p for p in validate(broken, "1h"))


def test_validate_catches_off_grid_timestamps(candles):
    broken = candles.copy()
    broken["ts"] = broken["ts"] + 7
    assert any("격자" in p for p in validate(broken, "1h"))


def test_resample_folds_minute_bars_into_five():
    # 시작을 5분 격자에 맞춰 둔다. 어긋나면 첫 버킷이 반쪽이라 13개가 나오는데,
    # 그건 resample 이 틀린 게 아니라 실제로 반쪽 봉이 있는 것이다.
    minutes = make_candles(count=60, timeframe="1m", start=1_699_999_800_000)
    five = resample(minutes, "5m")
    assert len(five) == 12
    # 굵은 봉의 고가는 그 구간 잔봉들의 최고가여야 한다.
    assert five["high"].iloc[0] == pytest.approx(minutes["high"].iloc[:5].max())
    assert five["open"].iloc[0] == pytest.approx(minutes["open"].iloc[0])
    assert five["close"].iloc[0] == pytest.approx(minutes["close"].iloc[4])
    assert five["volume"].iloc[0] == pytest.approx(minutes["volume"].iloc[:5].sum())


def test_resample_keeps_the_contract(candles):
    daily = resample(make_candles(count=240, timeframe="1h"), "1d")
    assert validate(daily, "1d") == []
