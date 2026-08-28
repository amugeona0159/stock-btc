"""종목 추천의 규칙.

추천은 만들기 쉽고 틀리기도 쉽다. 여기서 지키는 것은 셋이다:

1. **미래를 안 본다** — origin 뒤를 흔들어도 그 시점 순위가 안 변한다
2. **안 잰 건 안 내놓는다** — 측정 결과가 없으면 그럴듯한 목록 대신 빈 답
3. **부호를 미리 정하지 않는다** — IC 부호가 폴드마다 뒤집히면 그 축은 버린다
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from marketlens.screen import factors, ic, rank, universe
from tests.conftest import make_candles


# --- 표는 한 벌 ---------------------------------------------------------

def test_peers_and_universe_are_the_same_table():
    """학습 동료와 추천 후보가 갈라지면 '재 본 목록'과 '추천하는 목록'이 달라진다."""
    from marketlens.api.routes import PEERS

    assert PEERS is universe.UNIVERSE


def test_every_market_is_wide_enough_to_rank():
    """다섯 종목에서 상위 3개를 고르는 건 순위가 아니라 나열이다."""
    for provider, symbols in universe.UNIVERSE.items():
        assert len(symbols) >= universe.MIN_BREADTH, provider
        assert len(set(symbols)) == len(symbols), f"{provider}: 중복"


def test_calendar_axes_are_not_candidates():
    """같은 시각이면 모든 종목이 같은 값이라 순위가 안 갈린다."""
    for axis in ("hour_sin", "hour_cos", "weekday_sin", "month_sin", "quarter_progress"):
        assert axis not in factors.CANDIDATES


def test_every_candidate_exists_in_the_learning_table():
    """축을 여기서 새로 만들지 않는다 — 만들면 인과성 보증 밖으로 나간다."""
    from marketlens.forecast.ml import dataset

    for name in factors.CANDIDATES:
        assert name in dataset.FEATURE_COLUMNS, name


# --- 미래를 안 본다 -----------------------------------------------------

ORIGIN = 500


def _tampered(df: pd.DataFrame, at: int = ORIGIN) -> pd.DataFrame:
    from marketlens.core.candle import to_frame

    rows = df.to_dict("records")
    for i in range(at + 1, len(rows)):
        for key in ("open", "high", "low", "close"):
            rows[i][key] *= 3.0
        rows[i]["volume"] *= 50.0
    return to_frame(rows)


@pytest.fixture(scope="module")
def pair():
    clean = make_candles(count=700, seed=17)
    return clean, _tampered(clean)


def test_factor_panel_ignores_the_future(pair):
    clean, dirty = pair
    a, b = (factors.panel(df.iloc[: ORIGIN + 1].reset_index(drop=True), [], horizon=3)
            for df in (clean, dirty))
    pd.testing.assert_frame_equal(a.tail(1), b.tail(1), atol=1e-12)


def test_relative_only_looks_backwards():
    """평소대비 z 는 자기 과거로만 만든다. 뒤를 보면 오늘 값이 내일 바뀐다."""
    values = pd.Series(np.arange(400, dtype="float64") + np.sin(np.arange(400)))
    full = factors.relative(values)
    cut = factors.relative(values.iloc[:300])
    # 300번째까지의 값은 뒤를 잘라도 그대로여야 한다.
    pd.testing.assert_series_equal(full.iloc[:300], cut, atol=1e-12)


def test_relative_needs_history():
    """창의 절반도 안 쌓였으면 값이 없다. 억지로 채우면 초반이 전부 잡음이다."""
    values = pd.Series(np.random.default_rng(0).normal(size=200))
    out = factors.relative(values, span=120)
    assert out.iloc[:59].isna().all()
    assert out.iloc[119:].notna().all()


def test_forward_label_is_not_a_candidate():
    """라벨이 팩터 목록에 새어 들어가면 IC 가 1에 가깝게 나온다."""
    assert not any(name.startswith("fwd") for name in factors.all_candidates())


# --- IC ------------------------------------------------------------------

def _panel(rng, symbols=8, bars=1400, leak=0.0):
    """합성 표. `leak` 을 올리면 팩터가 미래를 그만큼 안다."""
    rows = []
    for s in range(symbols):
        fwd = rng.normal(size=bars) * 0.02
        noise = rng.normal(size=bars)
        rows.append(pd.DataFrame({
            "ts": np.arange(bars) * 86_400_000,
            "symbol": f"S{s}",
            "signal": leak * fwd / 0.02 + (1 - leak) * noise,
            "junk": rng.normal(size=bars),
            "fwd": fwd,
            "fwd_abs": np.abs(fwd),
        }))
    return pd.concat(rows, ignore_index=True).sort_values("ts").reset_index(drop=True)


def test_pure_noise_is_rejected():
    """잡음이 '쓸 만한 축'으로 통과하면 나머지 숫자를 못 믿는다."""
    panel = _panel(np.random.default_rng(1), leak=0.0)
    score = ic.measure(panel, "junk", "fwd", "direction", 86_400_000)
    assert not score.usable
    assert abs(score.ic) < 0.1


def test_a_real_signal_is_found_with_the_right_sign():
    panel = _panel(np.random.default_rng(2), leak=0.6)
    score = ic.measure(panel, "signal", "fwd", "direction", 86_400_000)
    assert score.usable
    assert score.ic > 0.2
    assert all(v > 0 for v in score.fold_ic), "부호가 폴드마다 같아야 통과한다"


def test_sign_flips_are_rejected_even_when_the_mean_is_big():
    """평균만 보면 한 폴드에서 크게 이긴 축이 나머지를 다 져도 살아남는다."""
    rng = np.random.default_rng(3)
    panel = _panel(rng)
    # 앞 절반은 강한 양의 신호, 뒤 절반은 강한 음의 신호.
    half = panel["ts"] < panel["ts"].median()
    panel["flip"] = np.where(half, panel["fwd"], -panel["fwd"]) * 50 \
        + rng.normal(size=len(panel)) * 0.2
    score = ic.measure(panel, "flip", "fwd", "direction", 86_400_000)
    assert not score.usable
    assert "부호" in score.reason


def test_spearman_needs_enough_symbols():
    assert np.isnan(ic._spearman(np.arange(3.0), np.arange(3.0)))
    assert ic._spearman(np.arange(8.0), np.arange(8.0)) == pytest.approx(1.0)
    assert ic._spearman(np.arange(8.0), -np.arange(8.0)) == pytest.approx(-1.0)


# --- 순위 ----------------------------------------------------------------

def _latest(rng, symbols=8):
    return {f"S{i}": pd.Series({f"atr_pct{factors.REL}": rng.normal(),
                                f"rsi{factors.REL}": rng.normal(),
                                "atr_pct": rng.normal()})
            for i in range(symbols)}


def _measured(ic_value=0.05, usable=True, factor=None):
    factor = factor or f"atr_pct{factors.REL}"
    entry = [{"factor": factor, "ic": ic_value, "usable": usable}]
    return {"horizons": {"1": {"move": entry, "direction": entry,
                              "moveSpread": {"relative": {"topMinusBottomPct": 0.9}},
                              "directionSpread": {"relative": {"topMinusBottomPct": 0.1}}}}}


def test_unmeasured_horizon_returns_nothing():
    """그럴듯한 목록보다 빈 화면이 낫다."""
    out = rank.build(_latest(np.random.default_rng(4)), _measured(), horizon=7)
    assert out["available"] is False
    assert "안 쟀다" in out["reason"]


def test_too_few_symbols_returns_nothing():
    latest = _latest(np.random.default_rng(5), symbols=3)
    out = rank.build(latest, _measured(), horizon=1)
    assert out["available"] is False


def test_raw_factors_are_not_used_in_the_score():
    """원값 축은 '원래 많이 움직이는 종목'이라는 고정 순위라 오늘의 답이 아니다."""
    measured = _measured(factor="atr_pct")          # 평소대비가 아닌 원값
    out = rank.build(_latest(np.random.default_rng(6)), measured, horizon=1)
    assert out["available"] is True
    assert out["quality"]["move"]["factors"] == 0


def test_ranking_is_ordered_and_explained():
    out = rank.build(_latest(np.random.default_rng(7)), _measured(), horizon=1, limit=5)
    assert out["available"] is True
    assert len(out["items"]) == 5
    scores = [item["move"] for item in out["items"]]
    assert scores == sorted(scores, reverse=True)
    assert out["items"][0]["why"], "왜 위에 있는지가 비어 있으면 안 된다"
    assert out["sortedBy"] == "move"
    assert out["family"] == "relative"


def test_the_note_does_not_promise_direction():
    """'오를 순서'로 읽히면 이 화면은 해롭다."""
    out = rank.build(_latest(np.random.default_rng(8)), _measured(), horizon=1)
    assert "오를 순서가 아니다" in out["note"]


def test_measured_spread_travels_with_the_ranking():
    """순위만 보여 주고 과거 성적을 숨기면 '1등이니까 좋은 것'으로 읽힌다."""
    out = rank.build(_latest(np.random.default_rng(9)), _measured(), horizon=1)
    assert out["quality"]["move"]["topMinusBottomPct"] == 0.9


def test_score_ignores_the_size_of_ic_only_the_sign():
    """크기까지 쓰면 잰 구간에 무게를 맞춘 셈이 된다."""
    latest = _latest(np.random.default_rng(10))
    small = rank.build(latest, _measured(ic_value=0.02), horizon=1)
    large = rank.build(latest, _measured(ic_value=0.40), horizon=1)
    assert [i["symbol"] for i in small["items"]] == [i["symbol"] for i in large["items"]]
    assert small["items"][0]["move"] == large["items"][0]["move"]


def test_flipping_the_ic_sign_flips_the_ranking():
    latest = _latest(np.random.default_rng(11))
    up = rank.build(latest, _measured(ic_value=0.05), horizon=1)
    down = rank.build(latest, _measured(ic_value=-0.05), horizon=1)
    assert [i["symbol"] for i in up["items"]] == [i["symbol"] for i in down["items"]][::-1]
