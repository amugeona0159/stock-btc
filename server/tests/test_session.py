"""봉이 언제 끝나나 — 장 마감을 아는 규칙.

`ts + 봉길이 <= 지금` 은 암호화폐에는 맞지만 주식에는 틀리다. 코스피 일봉은
**15:30 KST 에 끝나는데** 그 식으로는 다음날 09:00 KST 에야 닫힌다. 그동안 예측과
추천은 하루 전 데이터로 돌아간다 — 실제로 29일 아침에 27일까지만 보고 예측했다.

여기서 지키는 것 셋:

1. 장이 끝나면 그날 봉은 닫힌다 (주식)
2. 장이 안 끝났으면 안 닫힌다 — **일찍 닫는 게 늦게 닫는 것보다 나쁘다.**
   덜 채워진 봉을 확정봉으로 쓰면 마지막 봉만 조용히 틀린다
3. 한 번 닫힌 봉은 다시 안 열린다
"""
from __future__ import annotations

import pandas as pd
import pytest

from marketlens.core.timeframe import SETTLE_MS, bar_closed, session_end_ms


def at(stamp: str) -> int:
    return int(pd.Timestamp(stamp).timestamp() * 1000)


DAY = at("2026-08-28 00:00:00+00:00")          # 일봉은 현지 날짜를 UTC 자정으로 찍는다


# --- 주식: 장 마감을 본다 ------------------------------------------------

def test_korea_closes_after_the_session_not_at_midnight():
    """코스피는 15:30 KST(06:30 UTC)에 끝난다. 자정까지 기다리면 17시간을 버린다."""
    assert bar_closed(DAY, "1d", at("2026-08-28 06:00:00+00:00"), "kr") is False
    assert bar_closed(DAY, "1d", at("2026-08-28 07:00:00+00:00"), "kr") is True


def test_us_closes_after_the_new_york_session():
    """NYSE 16:00 ET = 20:00 UTC (서머타임). 한국 시각으로는 다음날 새벽이다."""
    assert bar_closed(DAY, "1d", at("2026-08-28 19:00:00+00:00"), "us") is False
    assert bar_closed(DAY, "1d", at("2026-08-28 20:31:00+00:00"), "us") is True


def test_winter_time_is_handled_by_the_timezone():
    """겨울에는 16:00 ET = 21:00 UTC 다. 오프셋을 손으로 적으면 반년마다 틀린다."""
    day = at("2026-01-15 00:00:00+00:00")
    assert bar_closed(day, "1d", at("2026-01-15 20:31:00+00:00"), "us") is False
    assert bar_closed(day, "1d", at("2026-01-15 21:31:00+00:00"), "us") is True


def test_a_settle_margin_keeps_the_half_baked_bar_open():
    """마감 직후 값은 아직 확정이 아니다. 그때 닫으면 마지막 봉만 틀린다."""
    ended = session_end_ms(DAY, "kr")
    assert bar_closed(DAY, "1d", ended + 60_000, "kr") is False
    assert bar_closed(DAY, "1d", ended + SETTLE_MS, "kr") is True


# --- 암호화폐: 마감이 없다 -----------------------------------------------

def test_crypto_still_waits_for_midnight():
    """24시간 돌아가는 시장에는 장 마감이 없다. 시각 계산이 맞다."""
    assert session_end_ms(DAY, "crypto") is None
    assert bar_closed(DAY, "1d", at("2026-08-28 23:59:00+00:00"), "crypto") is False
    assert bar_closed(DAY, "1d", at("2026-08-29 00:00:00+00:00"), "crypto") is True


def test_an_unknown_market_falls_back_to_the_clock():
    """모르는 시장이면 예전 규칙 그대로. 추측해서 일찍 닫지 않는다."""
    assert bar_closed(DAY, "1d", at("2026-08-28 23:59:00+00:00"), "") is False
    assert bar_closed(DAY, "1d", at("2026-08-29 00:00:00+00:00"), "") is True


# --- 짧은 봉은 그대로 ----------------------------------------------------

@pytest.mark.parametrize("timeframe", ["1m", "15m", "1h", "4h"])
def test_intraday_bars_ignore_the_session(timeframe):
    """장중 봉은 시각 계산이 맞다. 여기에 마감을 끼우면 장 마감 뒤 봉이
    통째로 안 닫히거나 너무 일찍 닫힌다."""
    from marketlens.core.timeframe import to_ms

    start = at("2026-08-28 05:00:00+00:00")
    step = to_ms(timeframe)
    assert bar_closed(start, timeframe, start + step - 1, "kr") is False
    assert bar_closed(start, timeframe, start + step, "kr") is True


# --- 절대 뒤집히지 않는다 ------------------------------------------------

@pytest.mark.parametrize("market", ["kr", "us", "crypto", ""])
def test_once_closed_always_closed(market):
    """봉이 닫혔다 열리면 그 위에서 잰 성적이 전부 거짓이 된다."""
    seen_closed = False
    for minutes in range(0, 60 * 40, 20):          # 40시간을 20분 간격으로
        now = DAY + minutes * 60_000
        closed = bar_closed(DAY, "1d", now, market)
        if seen_closed:
            assert closed is True, f"{market}: {minutes}분에 다시 열렸다"
        seen_closed = seen_closed or closed
    assert seen_closed, f"{market}: 40시간이 지나도 안 닫혔다"


def test_the_clock_rule_still_closes_a_stale_bar():
    """마감 표가 틀려도 시각 계산이 결국 닫는다. 두 규칙을 OR 로 묶은 이유다."""
    assert bar_closed(DAY, "1d", DAY + 86_400_000, "화성증권") is True


# --- 프로바이더가 이걸 쓰는지 --------------------------------------------

def test_every_provider_uses_the_shared_rule():
    """`ts + step <= now` 를 프로바이더마다 다시 적으면 장 마감이 또 빠진다."""
    from pathlib import Path

    folder = Path(__file__).resolve().parents[1] / "marketlens" / "providers"
    for path in folder.glob("*.py"):
        body = path.read_text(encoding="utf-8")
        assert "+ step <= now" not in body, f"{path.name}: 시각 계산을 다시 적었다"


def test_korean_daily_bar_is_closed_the_same_evening():
    """이 테스트가 사용자가 겪은 그 증상이다 — 29일 아침에 28일 봉이 열려 있었다."""
    day28 = at("2026-08-28 00:00:00+00:00")
    morning29 = at("2026-08-29 00:00:00+09:00")          # 29일 09:00 KST
    assert bar_closed(day28, "1d", morning29, "kr") is True
