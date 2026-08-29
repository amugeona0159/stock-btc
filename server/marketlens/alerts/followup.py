"""알림이 나간 뒤 값이 어디로 갔는지.

기록이 "그때 알림이 왔었다"에서 끝나면 그건 목록이지 기록이 아니다. 뒤가 붙어야
나중에 셀 수 있다 — 그게 `fired.jsonl` 을 지우지 않는 이유이기도 하다.

## 맞았다·틀렸다를 여기서 정하지 않는다

나가는 것은 **변화율뿐**이다. `buy_below` 는 뒤가 오르면 반가운 알림이고
`target_above` 는 이미 목표에 닿아 나간 알림이라, 한 잣대로 점수를 매기면 그
순간 없는 성적을 만들어 내게 된다. 부호를 어떻게 읽을지는 보는 사람이 정한다.

## 어느 값과 비교하나

기준은 **알림이 나갈 때 닿은 값**(`entry["price"]`)이다. 설정값이 아니다 — 실제로
시장에 있었던 값이 그쪽이고, 알림을 받은 사람이 본 숫자도 그것이다.

## 봉으로 센다

`+1일` 은 "알림이 든 일봉의 한 봉 뒤"다. 그래서 주식은 **거래일**로 세어진다 —
장이 쉬는 날을 하루로 세면 연휴 뒤 알림마다 빈칸이 생긴다. **확정된 봉만 쓴다**:
마지막 봉은 종가가 아직 안 굳어서, 그걸 끼우면 제일 최근 기록만 조용히 틀린다.
"""
from __future__ import annotations

import asyncio
import bisect
import logging
from datetime import datetime, timezone

from ..providers import get as get_provider

log = logging.getLogger("marketlens.alerts")

# 하루·사흘·이레. 더 늘리면 표가 옆으로 길어지는데, 그보다 뒤는 알림과 상관없는
# 움직임이라 붙여 놔도 읽는 사람이 뭘 해야 할지 모른다.
HORIZONS = (1, 3, 7)
# 한 종목이 오래 걸려도 나머지 기록은 나와야 한다.
TIMEOUT = 20.0
MAX_BARS = 400


def _ms(iso: str) -> int | None:
    try:
        return int(datetime.fromisoformat(iso).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _iso(ms: float) -> str:
    return (datetime.fromtimestamp(ms / 1000, timezone.utc)
            .isoformat(timespec="seconds"))


def _point(stamps: list[int], closes: list[float], index: int,
           base: float) -> dict | None:
    if index < 0 or index >= len(stamps):
        return None
    close = closes[index]
    return {"at": _iso(stamps[index]), "price": close,
            "changePct": (close / base - 1.0) * 100.0}


async def _bars(provider_key: str, symbol: str, span_days: int):
    """확정된 일봉만. 마지막 봉은 종가가 아직 안 굳었다."""
    provider = get_provider(provider_key)
    limit = max(30, min(MAX_BARS, span_days + max(HORIZONS) + 30))
    frame = await asyncio.wait_for(provider.history(symbol, "1d", limit), TIMEOUT)
    if frame is None or frame.empty:
        return None
    if "closed" in frame.columns:
        frame = frame[frame["closed"].astype(bool)]
    return frame if not frame.empty else None


async def _group(provider_key: str, symbol: str, rows: list[dict]) -> tuple[dict, str]:
    """한 종목의 기록들을 한 번의 시세 조회로 끝낸다."""
    oldest = min(_ms(r["at"]) or 0 for r in rows)
    span = max(1, int((datetime.now(timezone.utc).timestamp() * 1000 - oldest)
                      / 86_400_000))
    try:
        frame = await _bars(provider_key, symbol, span)
    except asyncio.TimeoutError:
        return {}, "시세가 제때 안 왔다"
    except Exception as exc:                              # noqa: BLE001
        # 프로바이더 하나가 죽어도 나머지 종목의 기록은 나와야 한다.
        log.warning("기록 뒷값 실패 %s:%s — %s", provider_key, symbol, str(exc)[:80])
        return {}, str(exc)[:120]
    if frame is None:
        return {}, "확정된 일봉이 없다"

    stamps = [int(v) for v in frame["ts"].tolist()]
    closes = [float(v) for v in frame["close"].tolist()]

    out: dict[str, dict] = {}
    for row in rows:
        at = _ms(row["at"])
        base = float(row["price"])
        if at is None or base <= 0:
            continue
        # 알림이 든 봉. 그 봉의 종가가 아니라 **다음 봉부터** 가 뒤 이야기다.
        here = bisect.bisect_right(stamps, at) - 1
        if here < 0:
            continue
        after = {}
        for horizon in HORIZONS:
            point = _point(stamps, closes, here + horizon, base)
            if point is not None:
                after[str(horizon)] = point
        out[row["id"]] = {
            "base": base,
            "after": after,
            "latest": _point(stamps, closes, len(stamps) - 1, base),
        }
    return out, ""


async def measure(entries: list[dict]) -> dict:
    """기록들의 뒷값. 종목별로 묶어 시세를 한 번씩만 부른다.

    묶지 않으면 같은 종목에 걸린 알림 스무 건이 스무 번을 부른다 — 토스는 그 정도로
    429 를 준다.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for entry in entries:
        provider_key = entry.get("provider")
        symbol = entry.get("symbol")
        price = entry.get("price")
        if not provider_key or not symbol or not entry.get("at"):
            continue
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        groups.setdefault((provider_key, symbol), []).append(entry)

    if not groups:
        return {"outcomes": {}, "failed": {}, "horizons": list(HORIZONS)}

    keys = list(groups)
    results = await asyncio.gather(
        *[_group(key[0], key[1], groups[key]) for key in keys],
        return_exceptions=True,
    )

    outcomes: dict[str, dict] = {}
    failed: dict[str, str] = {}
    for key, result in zip(keys, results):
        name = f"{key[0]}:{key[1]}"
        if isinstance(result, BaseException):
            failed[name] = str(result)[:120]
            continue
        found, reason = result
        outcomes.update(found)
        if reason:
            failed[name] = reason
    return {"outcomes": outcomes, "failed": failed, "horizons": list(HORIZONS)}
