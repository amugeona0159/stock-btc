"""표준 캔들 한 벌.

세 시장이 주는 모양은 전부 다르다 — Binance 는 ms 정수, Upbit 는 KST ISO 문자열,
KIS 는 날짜와 시각이 두 필드로 쪼개져 온다. 그 차이는 프로바이더 안에서 끝낸다.
여기서부터 아래(지표·시그널·백테스트·화면)는 이 표만 안다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .timeframe import to_ms

# 열 순서도 계약이다. 테스트가 이 순서를 본다.
COLUMNS = ("ts", "open", "high", "low", "close", "volume", "closed")

_NUMERIC = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class Candle:
    """단일 봉. 스트림이 틱을 접을 때 쓴다. 대량 계산은 DataFrame 쪽."""

    ts: int  # 봉 시작 시각, UTC ms
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed: bool = True

    def as_row(self) -> dict:
        return {
            "ts": self.ts,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "closed": self.closed,
        }


def empty_frame() -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype="float64") for c in _NUMERIC})
    df.insert(0, "ts", pd.Series(dtype="int64"))
    df["closed"] = pd.Series(dtype="bool")
    return df[list(COLUMNS)]


def to_frame(rows: list[dict] | list[Candle]) -> pd.DataFrame:
    """정규화된 dict/Candle 목록 → 표준 DataFrame.

    ts 로 정렬하고 중복 ts 는 뒤엣것을 남긴다 — 스트림이 같은 봉을 여러 번 보내기 때문.
    """
    if not rows:
        return empty_frame()
    records = [r.as_row() if isinstance(r, Candle) else r for r in rows]
    df = pd.DataFrame.from_records(records)
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"캔들에 빠진 열: {missing}")
    df = df[list(COLUMNS)]
    df["ts"] = df["ts"].astype("int64")
    for c in _NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["closed"] = df["closed"].astype("bool")
    df = df.drop_duplicates(subset="ts", keep="last").sort_values("ts")
    return df.reset_index(drop=True)


def upsert(df: pd.DataFrame, candle: Candle) -> pd.DataFrame:
    """봉 하나를 덮어쓰거나 뒤에 붙인다. 실시간 갱신 경로가 이걸 쓴다."""
    if len(df) and df["ts"].iloc[-1] == candle.ts:
        df = df.copy()
        for k, v in candle.as_row().items():
            df.iloc[-1, df.columns.get_loc(k)] = v
        return df
    return to_frame(df.to_dict("records") + [candle.as_row()])


def closed_only(df: pd.DataFrame) -> pd.DataFrame:
    """확정된 봉만. 시그널 판정·백테스트·ML 라벨은 반드시 이걸 통과해서 본다.

    미확정 봉을 섞으면 장중에 떴다 사라지는 신호가 생기고, 그 순간 백테스트 숫자가
    실전과 갈라진다.
    """
    if "closed" not in df.columns:
        return df
    return df[df["closed"].astype(bool)].reset_index(drop=True)


def validate(df: pd.DataFrame, timeframe: str | None = None) -> list[str]:
    """계약 위반을 문장으로 돌려준다. 빈 목록이면 통과.

    프로바이더 계약 테스트가 네 구현 모두에 이 함수를 돌린다.
    """
    problems: list[str] = []
    if list(df.columns) != list(COLUMNS):
        problems.append(f"열 구성이 다르다: {list(df.columns)}")
        return problems
    if df.empty:
        return problems

    ts = df["ts"].to_numpy()
    if not np.all(np.diff(ts) > 0):
        problems.append("ts 가 단조 증가하지 않는다(중복이거나 뒤섞였다)")

    hi, lo = df["high"].to_numpy(), df["low"].to_numpy()
    op, cl = df["open"].to_numpy(), df["close"].to_numpy()
    if np.any(hi < lo):
        problems.append("high < low 인 봉이 있다")
    if np.any((op > hi) | (op < lo) | (cl > hi) | (cl < lo)):
        problems.append("시가/종가가 고가-저가 범위 밖이다")
    if np.any(df["volume"].to_numpy() < 0):
        problems.append("거래량이 음수다")

    closed = df["closed"].to_numpy()
    if np.any(~closed[:-1]):
        problems.append("마지막이 아닌 봉이 미확정이다")

    if timeframe:
        step = to_ms(timeframe)
        if np.any(ts % step != 0):
            problems.append(f"봉 시작 시각이 {timeframe} 격자에 안 맞는다")
    return problems


def assert_valid(df: pd.DataFrame, timeframe: str | None = None) -> pd.DataFrame:
    problems = validate(df, timeframe)
    if problems:
        raise ValueError("캔들 계약 위반: " + "; ".join(problems))
    return df


def resample(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """더 잘게 쪼개진 봉을 굵은 봉으로 접는다.

    KIS 처럼 1분봉만 주는 곳에서 5분봉을 만들 때 쓴다. 잘게 쪼개진 쪽이 없으면
    만들어낼 수 없으므로, 굵은 봉을 잘게 나누는 반대 방향은 지원하지 않는다.
    """
    if df.empty:
        return empty_frame()
    step = to_ms(timeframe)
    bucket = (df["ts"] // step) * step
    grouped = df.groupby(bucket, sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        # 구간의 마지막 조각이 미확정이면 그 굵은 봉도 미확정이다.
        closed=("closed", "last"),
    )
    grouped.insert(0, "ts", grouped.index.astype("int64"))
    return to_frame(grouped.to_dict("records"))
