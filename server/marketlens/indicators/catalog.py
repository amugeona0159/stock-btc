"""지표 카탈로그 — 목록을 얻는 곳은 여기 하나뿐이다.

지표를 추가하는 절차:
  1. 카테고리 파일(trend/momentum/volatility/volume/levels/structure/patterns)에
     `@indicator(IndicatorSpec(...))` 로 함수 하나를 쓴다.
  2. 새 카테고리 파일을 만들었다면 아래 import 에 한 줄 더한다.
끝이다. API·화면·기본 세트가 전부 이 카탈로그를 읽으므로 다른 데는 손대지 않는다.
"""
from __future__ import annotations

from ..core.registry import all_specs, compute, get_spec

# import 자체가 등록이다. 순서는 상관없지만 지우면 그 카테고리가 통째로 사라진다.
from . import levels as _levels  # noqa: F401
from . import momentum as _momentum  # noqa: F401
from . import patterns as _patterns  # noqa: F401
from . import structure as _structure  # noqa: F401
from . import trend as _trend  # noqa: F401
from . import volatility as _volatility  # noqa: F401
from . import volume as _volume  # noqa: F401

CATEGORY_LABELS = {
    "trend": "추세",
    "momentum": "모멘텀",
    "volatility": "변동성",
    "volume": "거래량",
    "level": "레벨",
    "structure": "구조",
    "pattern": "패턴",
}

# 화면을 처음 열었을 때 켜져 있는 지표. 많이 켜면 첫 화면이 읽히지 않는다.
DEFAULT_SET = (
    {"key": "ma", "params": {"period": 20, "kind": "ema"}},
    {"key": "ma", "params": {"period": 60, "kind": "ema"}},
    {"key": "bbands", "params": {}},
    {"key": "rsi", "params": {}},
    {"key": "macd", "params": {}},
)


def catalog() -> list[dict]:
    """프론트로 나가는 전체 표."""
    return [spec.to_dict() for spec in all_specs()]


def categories() -> list[dict]:
    seen = {spec.category for spec in all_specs()}
    return [{"key": k, "label": v} for k, v in CATEGORY_LABELS.items() if k in seen]


__all__ = ["catalog", "categories", "compute", "get_spec", "all_specs",
           "CATEGORY_LABELS", "DEFAULT_SET"]
