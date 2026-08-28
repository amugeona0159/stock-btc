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
