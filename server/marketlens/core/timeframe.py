"""타임프레임 하나를 밀리초로 푸는 곳. 다른 데서 60*1000 을 다시 적지 말 것."""
from __future__ import annotations

import re

_UNIT_MS = {
    "s": 1_000,
    "m": 60_000,
    "h": 3_600_000,
    "d": 86_400_000,
    "w": 604_800_000,
}

_PATTERN = re.compile(r"^(\d+)([smhdw])$")

# 화면 선택지. 프로바이더가 지원하지 못하는 건 각자 거절한다.
SUPPORTED = ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w")


def to_ms(timeframe: str) -> int:
    m = _PATTERN.match(timeframe.strip().lower())
    if not m:
        raise ValueError(f"알 수 없는 타임프레임: {timeframe!r} (예: 1m, 15m, 4h, 1d)")
    amount, unit = int(m.group(1)), m.group(2)
    if amount <= 0:
        raise ValueError(f"타임프레임은 양수여야 한다: {timeframe!r}")
    return amount * _UNIT_MS[unit]


def floor_ts(ts_ms: int, timeframe: str) -> int:
    """그 시각이 속한 봉의 시작 시각. 봉 경계는 에폭 기준으로 자른다."""
    step = to_ms(timeframe)
    return ts_ms - (ts_ms % step)


def next_ts(ts_ms: int, timeframe: str) -> int:
    return floor_ts(ts_ms, timeframe) + to_ms(timeframe)


# --- 봉이 끝났나 -------------------------------------------------------
#
# `ts + step <= now` 는 암호화폐에는 맞지만 주식에는 틀리다. 코스피 일봉은
# **15:30 KST 에 끝나는데** 이 식으로는 다음날 09:00 KST 에야 닫힌다. 그동안
# 예측·추천은 하루 전 데이터를 보고 돌아간다 — 29일 아침에 27일까지만 보고
# 예측하던 게 이것 때문이다.
#
# 그래서 **정규장 마감**을 표로 둔다. 마감이 지난 그날 봉은 더 안 변한다.
SESSION_END = {
    "kr": ("Asia/Seoul", 15, 30),        # 코스피·코스닥 정규장
    "us": ("America/New_York", 16, 0),   # NYSE·나스닥 정규장 (서머타임은 tz 가 처리)
}
# 마감 뒤 이만큼은 기다린다. 종가·거래량이 확정돼 들어오기까지 몇 분 걸리고,
# 그 사이 값을 확정봉으로 쓰면 마지막 봉만 조용히 틀린다.
SETTLE_MS = 30 * 60_000
# 장 마감을 따지는 건 일봉부터다. 그보다 짧은 봉은 시각 계산이 맞다.
_SESSION_TIMEFRAMES = ("1d",)


def session_end_ms(day_ts_ms: int, market: str) -> int | None:
    """그 봉이 담는 **현지 달력 날짜**의 정규장 마감 시각(ms).

    일봉은 프로바이더가 현지 날짜를 UTC 자정으로 찍어 준다(`toss._local_day_ms`,
    야후도 결과적으로 같다). 그러니 UTC 날짜를 그대로 현지 날짜로 읽으면 된다.
    장 마감 표가 없는 시장(암호화폐)은 None — 24시간 돌아 마감이 없다.
    """
    found = SESSION_END.get(market)
    if found is None:
        return None
    zone, hour, minute = found
    import pandas as pd

    day = pd.Timestamp(day_ts_ms, unit="ms", tz="UTC").date()
    local = pd.Timestamp(day, tz=zone) + pd.Timedelta(hours=hour, minutes=minute)
    return int(local.tz_convert("UTC").timestamp() * 1000)


def bar_closed(ts_ms: int, timeframe: str, now_ms: int, market: str = "") -> bool:
    """이 봉이 더 안 변하나. **프로바이더마다 다시 적지 말 것.**

    두 규칙을 OR 로 묶는다:
    - 시각 계산: 봉 길이가 다 지났다 (암호화폐·분봉·시간봉)
    - 장 마감: 그날 정규장이 끝났다 (주식 일봉)

    OR 인 게 중요하다. 마감 표가 틀려도 시각 계산이 결국 봉을 닫으므로,
    한 번 닫힌 봉이 다시 열리는 일은 없다.
    """
    if ts_ms + to_ms(timeframe) <= now_ms:
        return True
    if timeframe not in _SESSION_TIMEFRAMES:
        return False
    ended = session_end_ms(ts_ms, market)
    return ended is not None and now_ms >= ended + SETTLE_MS
