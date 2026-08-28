"""지표 검증.

정답지를 다른 라이브러리에서 빌려오지 않는다 — 버전마다 값이 달라 무엇이 옳은지 모르게 된다.
손으로 계산한 고정값과, 정의상 반드시 참이어야 하는 성질로 잡는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from marketlens.core.registry import all_specs, compute, get_spec
from marketlens.indicators import _math as m
from marketlens.indicators import catalog  # noqa: F401  (import 이 곧 등록)
from tests.conftest import make_candles


# --- 손계산 고정값 ----------------------------------------------------------

def test_sma_matches_hand_calculation():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = m.sma(s, 3)
    assert np.isnan(result.iloc[1])
    assert result.iloc[2] == pytest.approx(2.0)   # (1+2+3)/3
    assert result.iloc[4] == pytest.approx(4.0)   # (3+4+5)/3


def test_ema_is_seeded_with_sma():
    """EMA 시드 관례가 이 프로젝트의 계약이다.

    첫 값으로 시드하는 구현으로 바꾸면 여기가 깨진다 — 깨지는 게 맞다.
    화면의 20EMA 와 백테스트의 20EMA 가 달라지는 순간이 그때다.
    """
    s = pd.Series([10.0, 11.0, 12.0, 13.0])
    result = m.ema(s, 3)
    assert np.isnan(result.iloc[1])
    assert result.iloc[2] == pytest.approx(11.0)  # SMA(10,11,12)
    # alpha = 2/(3+1) = 0.5 → 0.5*13 + 0.5*11 = 12.0
    assert result.iloc[3] == pytest.approx(12.0)


def test_rma_uses_wilder_alpha():
    s = pd.Series([4.0, 6.0, 8.0, 10.0])
    result = m.rma(s, 2)
    assert result.iloc[1] == pytest.approx(5.0)   # SMA(4,6)
    # alpha = 1/2 → 0.5*8 + 0.5*5 = 6.5
    assert result.iloc[2] == pytest.approx(6.5)


def test_wma_weights_recent_bars_more():
    s = pd.Series([1.0, 2.0, 3.0])
    # (1*1 + 2*2 + 3*3) / 6
    assert m.wma(s, 3).iloc[2] == pytest.approx(14.0 / 6.0)


def test_true_range_uses_previous_close():
    df = pd.DataFrame({
        "open": [10.0, 12.0], "high": [11.0, 15.0],
        "low": [9.0, 11.0], "close": [10.5, 14.0],
    })
    tr = m.true_range(df)
    assert tr.iloc[0] == pytest.approx(2.0)   # 첫 봉은 고-저
    # max(15-11=4, |15-10.5|=4.5, |11-10.5|=0.5)
    assert tr.iloc[1] == pytest.approx(4.5)


# --- 정의상 반드시 참인 성질 -------------------------------------------------

def test_rsi_stays_in_range(candles):
    value = compute("rsi", candles, {})["value"].dropna()
    assert len(value) > 100
    assert value.between(0, 100).all()


def test_atr_is_never_negative(candles):
    assert (compute("atr", candles, {})["value"].dropna() >= 0).all()


def test_bollinger_bands_are_ordered(candles):
    bb = compute("bbands", candles, {}).dropna()
    assert (bb["upper"] >= bb["middle"]).all()
    assert (bb["middle"] >= bb["lower"]).all()


def test_donchian_contains_price(candles):
    dc = compute("donchian", candles, {}).dropna()
    window = candles.loc[dc.index]
    assert (dc["upper"] >= window["high"]).all()
    assert (dc["lower"] <= window["low"]).all()


def test_stochastic_stays_in_range(candles):
    stoch = compute("stoch", candles, {}).dropna()
    assert stoch["k"].between(0, 100).all()
    assert stoch["d"].between(0, 100).all()


def test_williams_r_is_negative_range(candles):
    value = compute("willr", candles, {})["value"].dropna()
    assert value.between(-100, 0).all()


def test_fisher_transform_is_finite(candles):
    """|x| 가 1 에 닿으면 ln 이 발산한다. 클램프가 그걸 막는지 본다."""
    fisher = compute("fisher", candles, {})["fisher"].dropna()
    assert len(fisher) > 100
    assert np.isfinite(fisher).all()


def test_fisher_survives_flat_price():
    """가격이 전혀 안 움직이면 정규화 분모가 0이 된다. 터지지 않아야 한다."""
    flat = make_candles(count=80)
    for column in ("open", "high", "low", "close"):
        flat[column] = 100.0
    result = compute("fisher", flat, {})
    assert result["fisher"].isna().all()


# --- 일목균형표 -------------------------------------------------------------

def test_ichimoku_spans_are_displaced_forward(candles):
    """선행스팬은 26봉 앞으로 밀린 값이다. 밀리지 않으면 구름이 가격과 겹쳐 그려진다."""
    ichi = compute("ichimoku", candles, {})
    disp = 26
    # span_a 는 이미 밀린 것, span_a_lead 는 밀기 전 원본이다.
    shifted = ichi["span_a_lead"].shift(disp)
    pd.testing.assert_series_equal(ichi["span_a"], shifted, check_names=False)


def test_ichimoku_tenkan_is_midpoint_of_range(candles):
    ichi = compute("ichimoku", candles, {})
    expected = (candles["high"].rolling(9).max() + candles["low"].rolling(9).min()) / 2
    pd.testing.assert_series_equal(ichi["tenkan"], expected, check_names=False)


def test_ichimoku_chikou_tail_is_empty(candles):
    """후행스팬의 마지막 26봉은 비어 있어야 한다.

    값이 차 있으면 그건 아직 오지 않은 종가를 들고 있다는 뜻이고, 시그널이 미래를 본다.
    """
    chikou = compute("ichimoku", candles, {})["chikou"]
    assert chikou.tail(26).isna().all()
    assert chikou.iloc[-27] == pytest.approx(candles["close"].iloc[-1])


# --- 피보나치 ---------------------------------------------------------------

def test_fibonacci_levels_lie_between_swing_ends(candles):
    fib = compute("fibonacci", candles, {})
    start, end = fib["start"].iloc[-1], fib["end"].iloc[-1]
    assert np.isfinite(start) and np.isfinite(end)
    low, high = min(start, end), max(start, end)
    for column in ("r0236", "r0382", "r0500", "r0618", "r0786"):
        level = fib[column].iloc[-1]
        assert low <= level <= high, f"{column} 이 다리 밖으로 나갔다"


def test_fibonacci_half_level_is_midpoint(candles):
    fib = compute("fibonacci", candles, {})
    start, end = fib["start"].iloc[-1], fib["end"].iloc[-1]
    assert fib["r0500"].iloc[-1] == pytest.approx((start + end) / 2)


# --- 카탈로그 전수 검사 ------------------------------------------------------

@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.key)
def test_every_indicator_matches_its_spec(spec, candles):
    """등록된 지표 전부가 스펙대로 도는지.

    출력 열 이름과 길이는 `compute` 가 검사하므로, 여기서는 실제로 값이 나오는지까지 본다.
    스펙만 맞고 전부 NaN 이면 화면에는 아무것도 안 그려진다.
    """
    result = compute(spec.key, candles, {})
    assert list(result.columns) == [o.key for o in spec.outputs]
    assert len(result) == len(candles)

    required = [o for o in spec.outputs if not o.optional]
    for out in required:
        assert result[out.key].notna().any(), f"{spec.key}.{out.key} 이 전부 비었다"


@pytest.mark.parametrize("spec", all_specs(), ids=lambda s: s.key)
def test_every_indicator_survives_short_input(spec):
    """봉이 워밍업보다 적어도 터지지 않고 NaN 으로 나와야 한다.

    새 종목을 처음 열면 늘 이 상태를 지난다.
    """
    tiny = make_candles(count=5, seed=1)
    result = compute(spec.key, tiny, {})
    assert len(result) == len(tiny)


def test_unknown_indicator_is_rejected(candles):
    with pytest.raises(KeyError):
        get_spec("nope")


def test_unknown_param_is_rejected(candles):
    with pytest.raises(ValueError):
        compute("rsi", candles, {"perid": 14})


def test_param_range_is_enforced(candles):
    with pytest.raises(ValueError):
        compute("rsi", candles, {"period": 1})
