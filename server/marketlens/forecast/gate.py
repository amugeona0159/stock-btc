"""기권 — 못 맞히는 자리에서 말을 안 하는 규칙.

`scripts/study.py` 가 과거 판을 쌓아 "어떤 조건에서 틀렸나"를 갈라 보고, 거기서
나온 규칙을 **한 번도 안 본 구간에서 확인한 뒤** `learning/study/gate.json` 에 적는다.
이 파일은 그걸 읽어 오늘의 예측에 그대로 적용한다.

## 왜 기권인가

짧은 지평의 방향 예측은 문헌에서도 잡음에 가깝고, 이 저장소가 잰 것도 그랬다.
그런 판에서 정확도를 올리는 확실한 길은 **더 잘 맞히는 것**이 아니라 **못 맞히는
자리에서 안 맞히는 것**이다. 기권한 판은 방향을 말하지 않고 변동성 기준선 밴드만
남긴다 — 밴드는 방향과 달리 실제로 잘 맞는다(적중 78~80%).

## 규칙을 여기서 만들지 않는다

임계값을 손으로 적지 말 것. 여기 있는 건 **읽고 적용하는 코드뿐**이고, 무엇을
기권할지는 전부 측정에서 온다. 손으로 적는 순간 그건 측정이 아니라 믿음이 된다.
"""
from __future__ import annotations

import json
from pathlib import Path

# `api/learning.py` 와 같은 규칙 — 이 PC 가 잰 게 있으면 그쪽이 맞다.
_ROOT = Path(__file__).resolve().parents[3]
DIRS = (_ROOT / "learning-local" / "study", _ROOT / "learning" / "study")
FILE = "gate.json"


def _read() -> dict:
    for folder in DIRS:
        path = folder / FILE
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
    return {}


def rule() -> dict | None:
    """지금 쓰는 규칙. 최종 구간에서 못 이겼으면 None 이다."""
    found = _read().get("rule")
    return found if isinstance(found, dict) and found.get("condition") else None


def status() -> dict:
    """화면에 붙일 근거. 규칙만 보여주고 성적을 숨기면 그냥 마법 규칙이 된다."""
    raw = _read()
    return {
        "available": bool(rule()),
        "label": raw.get("label"),
        "holdout": raw.get("holdout"),
        "holdoutLooks": raw.get("holdoutLooks"),
        "trials": raw.get("trials"),
        "updated": raw.get("updated"),
    }


def abstains(conditions: dict) -> tuple[bool, str]:
    """이 판에서 말을 안 해야 하나. (기권할까, 왜)

    `conditions` 는 `dataset.build` 의 마지막 행 + 예측이 내놓은 축
    (`band_atr`·`move_atr`·`prob_up`). 조건이 표에 없으면 **기권하지 않는다** —
    모르는 조건으로 입을 다무는 건 규칙이 아니라 사고다.
    """
    found = rule()
    if not found:
        return False, ""
    value = conditions.get(found["condition"])
    if value is None:
        return False, ""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return False, ""
    threshold = float(found["threshold"])
    hit = value < threshold if found["op"] == "<" else value > threshold
    if not hit:
        return False, ""
    raw = _read()
    holdout = raw.get("holdout") or {}
    base, ruled = holdout.get("withoutRule"), holdout.get("withRule")
    edge = (f" (한 번도 안 본 구간에서 {base * 100:.1f}% → {ruled * 100:.1f}%)"
            if base is not None and ruled is not None else "")
    return True, f"{raw.get('label', '조건')} — 여기서는 방향을 말하지 않는다{edge}"
