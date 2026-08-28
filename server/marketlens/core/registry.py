"""지표 등록부.

이 파일의 요지는 하나다 — **공식은 코드가 아니라 데이터다.**
지표 하나 = 스펙 한 벌(이름·파라미터·출력 시리즈·수식) + 순수 함수 하나.
화면의 지표 패널은 이 스펙을 `GET /api/indicators` 로 받아 스스로 그린다.
프론트에 지표 목록을 다시 적는 순간 두 벌이 어긋나기 시작한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

import pandas as pd

Category = Literal["trend", "momentum", "volatility", "volume", "level", "structure", "pattern"]
Pane = Literal["price", "own"]
Draw = Literal["line", "area", "histogram", "band", "cloud", "marker", "level", "step"]


@dataclass(frozen=True)
class Param:
    """지표 하나가 받는 손잡이. 화면의 입력칸이 여기서 만들어진다."""

    key: str
    label: str
    default: float | int | str | bool
    kind: Literal["int", "float", "choice", "bool"] = "int"
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: tuple[str, ...] = ()

    def coerce(self, value):
        if value is None:
            return self.default
        if self.kind == "int":
            v = int(value)
        elif self.kind == "float":
            v = float(value)
        elif self.kind == "bool":
            v = bool(value)
        else:
            v = str(value)
            if self.choices and v not in self.choices:
                raise ValueError(f"{self.key}: {v!r} 은 {self.choices} 중에 없다")
            return v
        if self.min is not None and v < self.min:
            raise ValueError(f"{self.key}: {v} 는 최소 {self.min} 보다 작다")
        if self.max is not None and v > self.max:
            raise ValueError(f"{self.key}: {v} 는 최대 {self.max} 보다 크다")
        return v


@dataclass(frozen=True)
class Output:
    """지표가 내놓는 시리즈 하나. 어떻게 그릴지까지 여기서 정한다."""

    key: str
    label: str
    draw: Draw = "line"
    pane: Pane = "price"
    color: str = "accent"          # 웹의 색 토큰 이름. hex 를 여기 적지 말 것.
    pair: str | None = None        # band/cloud 의 짝이 되는 출력 key
    optional: bool = False         # 기본으로는 숨기는 보조선
    offset: int = 0                # 봉 단위 시프트. 양수 = 미래(일목 선행스팬)
    offset_param: str | None = None  # 시프트 폭이 파라미터에 달렸으면 그 키

    def shift_by(self, params: dict) -> int:
        """실제로 몇 봉 밀지. 일목 이동값처럼 파라미터가 정하는 경우가 있다."""
        if self.offset_param:
            return int(params[self.offset_param])
        return self.offset


@dataclass(frozen=True)
class IndicatorSpec:
    key: str
    name: str                       # 한글명
    category: Category
    formula: str                    # 사람이 읽는 수식 설명
    outputs: tuple[Output, ...]
    params: tuple[Param, ...] = ()
    pane: Pane = "price"            # 기본 패널. 출력이 따로 지정하면 그쪽이 이긴다.
    warmup: Callable[[dict], int] = field(default=lambda p: 0, repr=False, compare=False)
    source: str | None = None       # 참고 출처(있으면)

    def resolve(self, params: dict | None = None) -> dict:
        """넘어온 값에 기본값을 채우고 범위를 검사한 최종 파라미터."""
        given = params or {}
        unknown = set(given) - {p.key for p in self.params}
        if unknown:
            raise ValueError(f"{self.key}: 모르는 파라미터 {sorted(unknown)}")
        return {p.key: p.coerce(given.get(p.key)) for p in self.params}

    def min_bars(self, params: dict | None = None) -> int:
        return int(self.warmup(self.resolve(params)))

    def to_dict(self) -> dict:
        """프론트로 나가는 형태. warmup 같은 함수는 빼고 값만."""
        return {
            "key": self.key,
            "name": self.name,
            "category": self.category,
            "formula": self.formula,
            "pane": self.pane,
            "source": self.source,
            "params": [
                {
                    "key": p.key,
                    "label": p.label,
                    "default": p.default,
                    "kind": p.kind,
                    "min": p.min,
                    "max": p.max,
                    "step": p.step,
                    "choices": list(p.choices),
                }
                for p in self.params
            ],
            "outputs": [
                {
                    "key": o.key,
                    "label": o.label,
                    "draw": o.draw,
                    "pane": o.pane if o.pane != "price" or self.pane == "price" else self.pane,
                    "color": o.color,
                    "pair": o.pair,
                    "optional": o.optional,
                    "offset": o.offset,
                    "offsetParam": o.offset_param,
                }
                for o in self.outputs
            ],
        }


IndicatorFn = Callable[[pd.DataFrame, dict], pd.DataFrame]

_SPECS: dict[str, IndicatorSpec] = {}
_FUNCS: dict[str, IndicatorFn] = {}


def indicator(spec: IndicatorSpec) -> Callable[[IndicatorFn], IndicatorFn]:
    """계산 함수를 스펙에 묶는다.

    함수는 표준 캔들 DataFrame 과 확정된 파라미터를 받아, **입력과 같은 길이**의
    DataFrame 을 돌려준다. 열 이름은 스펙의 출력 key 와 정확히 같아야 한다.
    """

    def wrap(fn: IndicatorFn) -> IndicatorFn:
        if spec.key in _SPECS:
            raise RuntimeError(f"지표 키가 겹친다: {spec.key}")
        _SPECS[spec.key] = spec
        _FUNCS[spec.key] = fn
        return fn

    return wrap


def get_spec(key: str) -> IndicatorSpec:
    try:
        return _SPECS[key]
    except KeyError:
        raise KeyError(f"등록되지 않은 지표: {key!r}") from None


def all_specs() -> list[IndicatorSpec]:
    return sorted(_SPECS.values(), key=lambda s: (s.category, s.key))


def compute(key: str, df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """지표 하나를 계산한다. 출력 열이 스펙과 어긋나면 여기서 걸린다."""
    spec = get_spec(key)
    resolved = spec.resolve(params)
    out = _FUNCS[key](df, resolved)
    expected = [o.key for o in spec.outputs]
    if list(out.columns) != expected:
        raise RuntimeError(f"{key}: 출력 열이 스펙과 다르다 {list(out.columns)} != {expected}")
    if len(out) != len(df):
        raise RuntimeError(f"{key}: 출력 길이가 입력과 다르다 {len(out)} != {len(df)}")
    return out
