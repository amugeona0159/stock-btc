"""오늘 볼 종목 줄 세우기.

점수는 **잰 것만으로** 만든다. `learning/factors.json` 에 적힌 팩터 중 그 지평에서
`usable` 인 것만 쓰고, 부호도 거기 적힌 IC 의 부호를 따른다. 재지 않은 지평은
순위를 매기지 않고 "아직 안 쟀다"고 답한다 — 그럴듯한 목록을 만들어 내는 것보다
빈 화면이 낫다.

## 왜 z 점수를 평균하는가

무게를 IC 크기에 비례시키면 그 IC 를 잰 구간에 맞춰 무게를 고른 셈이 된다.
**쓸 만한 축을 고르는 데까지만 측정을 쓰고, 고른 뒤에는 동일 가중**으로 간다.
팩터 조합에서 동일 가중이 최적화 가중보다 표본 밖에서 나은 건 오래된 결과다.

## 두 점수를 따로 낸다

- `move` — 앞으로 크게 움직일 것 같은가. **"관심있게 볼 종목"은 이쪽이다.**
- `direction` — 어느 쪽으로 갈 것 같은가. 잰 IC 가 작으면 그대로 작다고 적는다.

섞어서 하나로 내지 않는다. 섞으면 "크게 움직인다"와 "오른다"가 구별되지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import factors as factor_table
from .universe import MIN_BREADTH

# 점수에 쓸 축의 갈래. **평소대비(`__rel`)만 쓴다.**
#
# 원값 축(변동성·베타·밴드폭)은 IC 가 3~10배 크게 나온다. 그런데 그건
# "DOGE 는 원래 BTC 보다 많이 움직인다"는 **고정 순위**다. 늘 맞지만 매일 같은
# 답을 내므로 "오늘 뭘 볼까"에는 쓸모가 없다 — 그 목록은 하루 뒤에도, 한 달 뒤에도
# 똑같다. 오늘의 정보는 자기 과거와 견준 쪽에만 있다.
#
# 실제로 잰 값(암호화폐 일봉, scripts/screen.py):
#   변동 3일 — 원값만 +2.15%p · 평소대비만 +0.94%p
#   방향 3일 — 원값만 +0.48%p · 평소대비만 +0.65%p (이쪽이 더 크고 단조롭다)
# 방향은 평소대비가 오히려 낫고, 변동은 원값이 크지만 그 크기가 고정 순위에서 온다.
FAMILY = "relative"


@dataclass
class Ranked:
    symbol: str
    move: float | None = None
    direction: float | None = None
    # 점수에 가장 크게 기여한 축들. 왜 이 종목이 위에 있는지 보여 준다.
    why: list[dict] = field(default_factory=list)
    last: float | None = None
    change_pct: float | None = None


def _payload(item: Ranked) -> dict:
    """화면으로 나가는 모양. 나머지 API 와 같이 camelCase 로 맞춘다."""
    return {"symbol": item.symbol, "move": item.move, "direction": item.direction,
            "why": item.why, "last": item.last, "changePct": item.change_pct}


def _z(values: pd.Series) -> pd.Series:
    """횡단면 z 점수. 표준편차가 0이면 전부 0 — 순위가 없다는 뜻이다."""
    std = values.std(ddof=0)
    if not np.isfinite(std) or std == 0:
        return pd.Series(0.0, index=values.index)
    return (values - values.mean()) / std


def score(rows: pd.DataFrame, usable: dict[str, float], top_reasons: int = 3) -> pd.DataFrame:
    """`rows`: 종목마다 한 행(index=symbol, 열=팩터). `usable`: 팩터 → IC.

    IC 의 **부호만** 쓴다. 크기는 안 쓴다 — 크기까지 쓰면 잰 구간에 무게를 맞춘 셈이다.
    """
    present = [f for f in usable if f in rows.columns and rows[f].notna().sum() >= MIN_BREADTH]
    if not present:
        return pd.DataFrame(index=rows.index, columns=["score"], dtype="float64")

    parts = {}
    for name in present:
        parts[name] = _z(rows[name].astype("float64").fillna(rows[name].median())) \
            * float(np.sign(usable[name]))
    contribution = pd.DataFrame(parts, index=rows.index)
    out = pd.DataFrame({"score": contribution.mean(axis=1)}, index=rows.index)
    # 왜 위에 있는지: 기여가 큰 축부터.
    out["why"] = [
        [{"factor": f, "label": factor_table.label(f), "z": round(float(row[f]), 3)}
         for f in row.abs().sort_values(ascending=False).index[:top_reasons]]
        for _, row in contribution.iterrows()
    ]
    return out


def build(latest: dict[str, pd.Series], measured: dict, horizon: int,
          limit: int = 10, prices: dict[str, tuple[float, float]] | None = None) -> dict:
    """오늘의 순위.

    `latest`: 종목 → 마지막 확정봉의 팩터 값
    `measured`: `learning/factors.json` 의 내용
    """
    if len(latest) < MIN_BREADTH:
        return {"available": False,
                "reason": f"횡단면 순위를 매기려면 {MIN_BREADTH}종목은 있어야 한다 "
                          f"(지금 {len(latest)}종목)"}

    per_horizon = (measured.get("horizons") or {}).get(str(horizon))
    if not per_horizon:
        return {"available": False,
                "reason": f"{horizon}봉 지평은 아직 안 쟀다 — "
                          f"`python scripts/screen.py` 로 먼저 재야 한다"}

    rows = pd.DataFrame(latest).T
    out: dict[str, dict] = {}
    quality: dict[str, dict] = {}
    for kind in ("move", "direction"):
        usable = {f["factor"]: f["ic"] for f in per_horizon.get(kind, [])
                  if f.get("usable") and f["factor"].endswith(factor_table.REL)}
        gap = (per_horizon.get(f"{kind}Spread") or {}).get(FAMILY, {})
        quality[kind] = {
            "factors": len(usable),
            "meanIc": round(float(np.mean([abs(v) for v in usable.values()])), 4)
            if usable else 0.0,
            # 이 점수가 과거에 실제로 상위/하위를 얼마나 갈랐나. 순위만 보여 주고
            # 이걸 숨기면 "1등이니까 좋은 것"으로 읽힌다.
            "topMinusBottomPct": gap.get("topMinusBottomPct"),
            "buckets": gap.get("buckets"),
            "used": [{"factor": f, "label": factor_table.label(f), "ic": round(ic, 4)}
                     for f, ic in sorted(usable.items(), key=lambda kv: -abs(kv[1]))],
        }
        out[kind] = score(rows, usable) if usable else pd.DataFrame(index=rows.index)

    ranked: list[Ranked] = []
    for symbol in rows.index:
        item = Ranked(symbol=str(symbol))
        for kind in ("move", "direction"):
            frame = out[kind]
            if "score" in frame.columns and np.isfinite(frame.loc[symbol, "score"]):
                setattr(item, kind, round(float(frame.loc[symbol, "score"]), 4))
        # 왜 볼 만한지는 **변동** 쪽 근거를 보여 준다. 그게 이 목록의 목적이다.
        if "why" in out["move"].columns:
            item.why = out["move"].loc[symbol, "why"]
        elif "why" in out["direction"].columns:
            item.why = out["direction"].loc[symbol, "why"]
        if prices and symbol in prices:
            item.last, item.change_pct = prices[symbol]
        ranked.append(item)

    key = "move" if quality["move"]["factors"] else "direction"
    ranked.sort(key=lambda r: -(getattr(r, key) if getattr(r, key) is not None else -9.9))
    return {
        "available": True,
        "horizon": horizon,
        "sortedBy": key,
        "family": FAMILY,
        "quality": quality,
        "items": [_payload(r) for r in ranked[:limit]],
        "breadth": len(rows),
        "note": "순위는 '앞으로 크게 움직일 것 같은 순서'다. 오를 순서가 아니다 — "
                "방향은 따로 적고, 그 점수는 변동보다 훨씬 약하다.",
    }
