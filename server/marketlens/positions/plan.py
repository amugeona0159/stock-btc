"""진입가에서 손절가와 목표를 뽑는다.

## 밴드에서 읽는다, 지어내지 않는다

이 도구가 실제로 맞히는 건 **폭**이다(밴드 82.2%, 방향 55.0%). 그래서 계획도 폭에서
나온다 — 80% 밴드의 아래끝이 손절가고, 기대값과 위끝이 목표다. `-5% 손절 +10% 익절`
같은 고정 비율을 쓰지 않는 이유는, 그 숫자가 이 도구가 잰 무엇과도 이어져 있지 않아서다.
종목마다 변동성이 다른데 같은 비율을 걸면, 조용한 종목은 영영 안 닿고 시끄러운 종목은
첫날 손절된다.

## 밴드가 없으면 ATR 로 내려간다

손으로 연 포지션에는 그날 추천의 밴드가 없다. 그때는 변동성(ATR)을 쓴다 — **모델이
아니라 시장이 준 폭**이다. 이것도 없으면 계획을 안 만든다. 임의의 비율을 채워 넣는
것보다 "근거가 없어 못 냈다"가 낫다.

## 목표는 둘이다

1차는 기대값, 2차는 밴드 위끝. 1차에서 절반을 덜어내면 남은 절반은 손절가를 본전으로
올린 채로 간다 — 그때부터 그 판은 잃지 않는다. 셋 이상 두지 않는 건, 지평이 1~3일이라
그 안에서 세 번 나눠 팔 만큼 값이 움직이지 않기 때문이다.
"""
from __future__ import annotations

# 1차 목표에서 덜어내는 몫. 절반인 건 "잃지 않는 자리로 옮기는 데 드는 최소"라서다 —
# 더 적게 덜면 본전까지 못 오고, 더 많이 덜면 2차가 남는 게 없다.
FIRST_PORTION = 0.5
# 밴드가 없을 때 쓰는 ATR 배수. 손절 1.5·1차 1.5·2차 3.0 — 손익비 1:1 과 1:2 다.
ATR_STOP, ATR_FIRST, ATR_SECOND = 1.5, 1.5, 3.0
# 목표가 진입가보다 이만큼도 안 높으면 목표로 치지 않는다. 수수료에 먹힌다.
MIN_EDGE = 0.002


def _target(entry: float, price: float, portion: float, label: str) -> dict | None:
    if price <= entry * (1 + MIN_EDGE):
        return None
    return {"price": round(price, 8), "portion": portion, "label": label,
            "ruleId": None, "hitAt": None, "settledAt": None,
            "filledShares": 0.0, "filledPrice": None}


def from_band(entry: float, band: list[float] | None,
              expected: float | None) -> dict | None:
    """추천이 준 80% 밴드에서. `band` 는 [아래끝%, 위끝%] 다."""
    if not band or len(band) != 2 or band[0] >= 0:
        # 아래끝이 0 이상이면 "안 내려간다"는 예측이라 손절가를 놓을 데가 없다.
        return None
    stop = entry * (1 + band[0] / 100.0)
    wanted = [
        _target(entry, entry * (1 + (expected or 0) / 100.0), FIRST_PORTION, "기대값"),
        _target(entry, entry * (1 + band[1] / 100.0), 1 - FIRST_PORTION, "밴드 위끝"),
    ]
    return _settle(entry, stop, [t for t in wanted if t], "band")


def from_atr(entry: float, atr_pct: float | None) -> dict | None:
    """밴드가 없을 때. `atr_pct` 는 가격 대비 ATR 비율(0.03 = 3%)."""
    if not atr_pct or atr_pct <= 0:
        return None
    stop = entry * (1 - ATR_STOP * atr_pct)
    wanted = [
        _target(entry, entry * (1 + ATR_FIRST * atr_pct), FIRST_PORTION, "ATR 1.5배"),
        _target(entry, entry * (1 + ATR_SECOND * atr_pct), 1 - FIRST_PORTION, "ATR 3배"),
    ]
    return _settle(entry, stop, [t for t in wanted if t], "atr")


def _settle(entry: float, stop: float, targets: list[dict], source: str) -> dict | None:
    if stop >= entry or not targets:
        return None
    # 하나만 남으면 그것이 전부를 가진다. 몫이 0.5 로 남으면 절반이 계획 없이 뜬다.
    if len(targets) == 1:
        targets[0] = {**targets[0], "portion": 1.0}
    return {"stop": round(stop, 8), "targets": targets, "source": source,
            # 진입가 대비 손절선까지의 거리. **양수로 낸다** — "이 판에서 걸린 것이
            # 원금의 몇 %인가" 로 읽히는 값이라 부호가 붙으면 손실처럼 보인다.
            "riskPct": round((1.0 - stop / entry) * 100.0, 2)}


def trail_to(position_entry: float, targets: list[dict], hit_price: float) -> float:
    """목표에 닿았는데 안 팔았을 때 손절가를 어디로 올리나.

    **본전이 먼저다.** 1차에 닿고 안 팔았으면 손절가를 진입가로 올려 그 판을 잃지
    않는 판으로 만든다. 그다음부터는 **직전에 닿은 목표**까지 따라 올린다 —
    닿았던 값은 시장에 실제로 있었으므로, 거기까지 되돌아오면 이유가 사라진 것이다.
    """
    hit = [t["price"] for t in targets
           if t.get("hitAt") and t["price"] < hit_price]
    return max([position_entry, *hit]) if hit else position_entry
