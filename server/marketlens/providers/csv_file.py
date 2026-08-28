"""로컬 CSV — 인터넷 없이 돌리는 경로.

테스트와 백테스트가 이걸 쓴다. 거래소가 막혀 있어도 지표·시그널·백테스트는 검증 가능해야 한다.
`data/` 아래에 `<심볼>_<타임프레임>.csv` 를 두면 그대로 읽는다.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

import pandas as pd

from ..core.candle import to_frame
from ..core.timeframe import SUPPORTED
from .base import Provider, ProviderError, ProviderInfo, register

DATA_DIR = Path(os.environ.get("MARKET_LENS_DATA", "data")).resolve()

# 흔한 열 이름을 표준 이름으로. 여기 없는 헤더는 그대로 쓴다.
ALIASES = {
    "date": "ts", "time": "ts", "timestamp": "ts", "datetime": "ts",
    "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume",
    "vol": "volume", "adj close": "close",
}


class CsvProvider(Provider):
    info = ProviderInfo(
        key="csv",
        name="로컬 CSV",
        market="offline",
        timeframes=SUPPORTED,
        requires_key=False,
        realtime=False,
        note=f"{DATA_DIR} 의 <심볼>_<타임프레임>.csv 를 읽는다",
    )

    @property
    def available(self) -> bool:
        return DATA_DIR.is_dir()

    @property
    def unavailable_reason(self) -> str:
        return "" if self.available else f"{DATA_DIR} 가 없다"

    def _path(self, symbol: str, timeframe: str) -> Path:
        candidate = (DATA_DIR / f"{symbol}_{timeframe}.csv").resolve()
        # 심볼이 경로로 새어 나가지 못하게 한다 - 이건 사용자 입력이다.
        if not str(candidate).startswith(str(DATA_DIR)):
            raise ProviderError(f"허용되지 않는 경로: {symbol!r}")
        return candidate

    async def history(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        path = self._path(symbol, timeframe)
        if not path.is_file():
            raise ProviderError(f"CSV 가 없다: {path}")
        rows: list[dict] = []
        with path.open(newline="", encoding="utf-8") as handle:
            for record in csv.DictReader(handle):
                clean = {ALIASES.get(k.strip().lower(), k.strip().lower()): v
                         for k, v in record.items() if k}
                try:
                    rows.append({
                        "ts": _to_ms(clean["ts"]),
                        "open": float(clean["open"]),
                        "high": float(clean["high"]),
                        "low": float(clean["low"]),
                        "close": float(clean["close"]),
                        "volume": float(clean.get("volume") or 0.0),
                        "closed": True,
                    })
                except (KeyError, ValueError):
                    continue
        if not rows:
            raise ProviderError(f"{path} 에서 캔들을 읽지 못했다")
        return to_frame(rows[-limit:])

    async def search(self, query: str) -> list[dict]:
        if not self.available:
            return []
        needle = query.lower()
        found = {p.name.rsplit("_", 1)[0] for p in DATA_DIR.glob("*.csv")}
        return [{"symbol": s, "label": s} for s in sorted(found) if needle in s.lower()][:30]


def _to_ms(value: str) -> int:
    text = value.strip()
    if text.isdigit():
        number = int(text)
        # 10자리는 초, 13자리는 밀리초. 둘 다 흔하게 섞여 들어온다.
        return number * 1000 if number < 10_000_000_000 else number
    return int(pd.Timestamp(text, tz="UTC").timestamp() * 1000)


register(CsvProvider())
