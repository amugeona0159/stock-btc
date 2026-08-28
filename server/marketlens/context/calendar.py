"""시간 축.

"날짜별·시간별·분기별" 은 서로 다른 축이고, 하나만 봐서는 안 된다. 같은 목요일이라도
분기 마지막 주의 목요일과 첫 주의 목요일은 다른 상황이다.

주의: 이 축들은 **예측에 직접 쓰지 않는다.** 캘린더 효과는 발표 이후 사라진 사례가
많아서(`research.library: calendar_effects`), 여기서는 "비슷한 상황"을 고를 때의
조건으로만 쓴다. 요일이 수익률을 만든다고 주장하는 게 아니다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# 거래소는 UTC 로 돌지만 사람의 하루는 지역시로 돈다. 아시아 오전/유럽 오전/미국 오전이
# 갈리는 자리를 UTC 시각으로 표시해 둔다.
SESSIONS = {
    "asia": (0, 8),
    "europe": (7, 16),
    "us": (13, 21),
}


@dataclass(frozen=True)
class CalendarAxis:
    key: str
    label: str
    cyclical: bool  # 원형 축(23시와 0시는 이웃)인지


AXES = (
    CalendarAxis("hour", "시각(UTC)", True),
    CalendarAxis("weekday", "요일", True),
    CalendarAxis("month_progress", "월 중 위치", False),
    CalendarAxis("quarter_progress", "분기 중 위치", False),
    CalendarAxis("month", "월", True),
    CalendarAxis("quarter", "분기", True),
    CalendarAxis("year_progress", "연중 위치", False),
)


def frame(ts_ms: pd.Series) -> pd.DataFrame:
    """봉 시각에서 캘린더 축을 뽑는다. 전부 0..1 로 맞춰 거리 계산에 바로 쓴다."""
    when = pd.to_datetime(ts_ms, unit="ms", utc=True)
    out = pd.DataFrame(index=ts_ms.index)

    out["hour"] = when.dt.hour + when.dt.minute / 60.0
    out["weekday"] = when.dt.weekday.astype("float64")
    out["month"] = when.dt.month.astype("float64")
    out["quarter"] = when.dt.quarter.astype("float64")

    days_in_month = when.dt.days_in_month.astype("float64")
    out["month_progress"] = (when.dt.day - 1) / (days_in_month - 1).replace(0, np.nan)

    # 분기 경계는 타임존을 뗀 채로 잡는다. tz 붙은 값에 to_period 를 걸면 pandas 가
    # 타임존을 버리면서 경고를 낸다 - 어차피 UTC 기준 분기라 뗐다 붙이나 같다.
    naive = when.dt.tz_localize(None)
    quarter = naive.dt.to_period("Q")
    quarter_start = quarter.dt.start_time
    span = (quarter.dt.end_time - quarter_start).dt.total_seconds()
    out["quarter_progress"] = (naive - quarter_start).dt.total_seconds() / span

    out["year_progress"] = (when.dt.dayofyear - 1) / 365.0

    # 원형 축은 sin/cos 로 편다. 23시와 0시를 23만큼 떨어진 것으로 재면
    # '같은 새벽'이 서로 제일 먼 상황이 된다.
    for axis, period in (("hour", 24.0), ("weekday", 7.0), ("month", 12.0), ("quarter", 4.0)):
        angle = 2 * np.pi * out[axis] / period
        out[f"{axis}_sin"] = np.sin(angle)
        out[f"{axis}_cos"] = np.cos(angle)

    for name, (start, end) in SESSIONS.items():
        out[f"session_{name}"] = ((out["hour"] >= start) & (out["hour"] < end)).astype("float64")

    return out


def describe(ts_ms: int) -> dict:
    """봉 하나의 캘린더 상황을 사람이 읽는 형태로. 근거 문장에 쓴다."""
    when = pd.Timestamp(ts_ms, unit="ms", tz="UTC")
    weekday = "월화수목금토일"[when.weekday()]
    sessions = [name for name, (start, end) in SESSIONS.items() if start <= when.hour < end]
    return {
        "iso": when.isoformat(),
        "text": f"{when.year}년 {when.month}월 {when.day}일 {weekday}요일 "
                f"{when.hour:02d}시(UTC) · {when.quarter}분기",
        "hour": int(when.hour),
        "weekday": int(when.weekday()),
        "month": int(when.month),
        "quarter": int(when.quarter),
        "sessions": sessions,
    }
