"""여러 번 시험하고 제일 좋은 걸 고를 때의 보정."""
from __future__ import annotations

import math

import numpy as np
import pytest

from marketlens.forecast import overfit


# --------------------------------------------------------------- 정규 분위수

@pytest.mark.parametrize("p, want", [
    (0.5, 0.0),
    (0.75, 0.6744897501960817),
    (0.95, 1.6448536269514722),
    (0.975, 1.959963984540054),
    (0.999, 3.090232306167813),
])
def test_normal_quantile_matches_known_values(p, want):
    """scipy 없이 쓰려고 직접 뒀다. 값이 틀리면 문턱이 통째로 틀어진다."""
    assert overfit._ppf(p) == pytest.approx(want, abs=1e-9)


def test_normal_quantile_rejects_impossible_input():
    for bad in (0.0, 1.0, -0.5, 2.0):
        with pytest.raises(ValueError):
            overfit._ppf(bad)


# ----------------------------------------------------------- 귀무 최고값

def test_expected_max_matches_simulation():
    """식이 맞는지는 실제로 뽑아 보면 안다. 여기서 어긋나면 식이 틀린 것이다."""
    rng = np.random.default_rng(0)
    for trials in (10, 100, 1000):
        simulated = float(np.mean([rng.standard_normal(trials).max()
                                   for _ in range(1500)]))
        assert overfit.expected_max(trials) == pytest.approx(simulated, abs=0.06)


def test_one_trial_needs_no_correction():
    """한 번만 시험했으면 고른 게 아니다 — 선택 편향이 없다."""
    assert overfit.expected_max(1) == 0.0
    assert overfit.expected_max(0) == 0.0


def test_more_trials_raise_the_bar():
    bars = [overfit.expected_max(n) for n in (2, 5, 20, 100, 5000)]
    assert bars == sorted(bars)


def test_the_bar_grows_slowly():
    """**로그 정도로만 자란다.** 시험을 100배 해도 문턱은 두 배가 채 안 된다.

    이게 중요한 이유: 잡음을 막자고 진짜 신호까지 막으면 안 된다.
    """
    assert overfit.expected_max(10000) < overfit.expected_max(100) * 2.0


def test_the_bar_scales_with_the_noise():
    """폭이 두 배면 문턱도 두 배. 단위가 섞이면 안 된다."""
    assert overfit.expected_max(50, 0.004) == pytest.approx(
        overfit.expected_max(50, 0.002) * 2.0)


def test_survives_needs_more_than_luck():
    spread = 0.0015
    # 서른 번 도전한 자리. 잡음만으로도 이만큼은 나온다.
    luck = overfit.expected_max(30, spread)
    assert not overfit.survives(luck * 0.9, trials=30, null_std=spread)
    assert overfit.survives(luck * 1.1, trials=30, null_std=spread)
    # 한 번만 도전했으면 조금이라도 이기면 된다.
    assert overfit.survives(1e-9, trials=1, null_std=spread)


# ------------------------------------------------------------------ p 값

def test_p_value_never_claims_certainty():
    """200번 돌려 한 번도 안 넘었다고 p=0 이라고 쓰면 '절대 우연이 아니다' 가 된다.

    실제로 말할 수 있는 건 '1/201 보다 작다' 까지다.
    """
    null = np.zeros(200)
    assert overfit.p_value(999.0, null) == pytest.approx(1 / 201)
    assert overfit.p_value(999.0, null) > 0.0


def test_p_value_counts_ties_against_us():
    null = np.array([1.0, 1.0, 1.0, 1.0])
    # 관측이 귀무와 같으면 '넘었다' 고 하면 안 된다.
    assert overfit.p_value(1.0, null) == pytest.approx(5 / 5)


def test_p_value_survives_an_empty_null():
    assert overfit.p_value(1.0, []) == 1.0
    assert overfit.p_value(1.0, [float("nan")]) == 1.0


# ------------------------------------------------------------------- PBO

def test_pbo_is_half_when_choosing_is_pure_noise():
    """후보가 전부 잡음이면 앞에서 1등이어도 뒤에서는 아무데나 간다 → 0.5 근처."""
    rng = np.random.default_rng(3)
    noise = rng.standard_normal((240, 12))
    got = overfit.pbo(noise, splits=8)
    assert got["pbo"] == pytest.approx(0.5, abs=0.2), got


def test_pbo_is_low_when_one_candidate_is_really_better():
    """진짜로 나은 후보가 있으면 앞에서도 뒤에서도 1등이다 → 0 에 가깝다."""
    rng = np.random.default_rng(3)
    data = rng.standard_normal((240, 12))
    data[:, 4] += 1.2                     # 이 후보만 실력이 있다
    got = overfit.pbo(data, splits=8)
    assert got["pbo"] < 0.1, got


def test_pbo_refuses_when_there_is_nothing_to_rank():
    """후보가 둘이면 순위가 둘뿐이라 숫자가 뜻을 잃는다. 조용히 0.5 를 내면 안 된다."""
    rng = np.random.default_rng(3)
    got = overfit.pbo(rng.standard_normal((240, 2)), splits=8)
    assert math.isnan(got["pbo"])
    assert "후보" in got["reason"]


def test_pbo_refuses_a_too_short_history():
    rng = np.random.default_rng(3)
    got = overfit.pbo(rng.standard_normal((6, 10)), splits=8)
    assert math.isnan(got["pbo"])


def test_pbo_rejects_odd_or_tiny_split_counts():
    rng = np.random.default_rng(3)
    for bad in (3, 7, 2):
        with pytest.raises(ValueError):
            overfit.pbo(rng.standard_normal((240, 10)), splits=bad)


# ------------------------------------------------------- 겹치는 라벨의 실질 표본

def test_effective_n_divides_by_the_horizon():
    """지평 10이면 행 30,000개가 독립 관측 3,000개다."""
    assert overfit.effective_n(30_000, 10) == 3_000
    assert overfit.effective_n(30_000, 1) == 30_000


def test_effective_n_survives_edges():
    assert overfit.effective_n(0, 10) == 0
    assert overfit.effective_n(5, 10) == 1        # 0 을 내면 나누기에서 터진다
    assert overfit.effective_n(100, 0) == 100


def test_uniqueness_is_flat_when_every_bar_has_a_label():
    """**표본 가중치를 안 넣은 근거.** 지평이 고정이고 봉마다 라벨이 하나면
    고유도가 안쪽에서 완전히 일정하다 — 가중치를 줘도 학습이 안 달라진다.

    이 성질이 깨지는 날(빠진 봉이 많은 종목을 들이는 날) 다시 재야 하므로 고정한다.
    """
    import numpy as np

    horizon, total = 10, 500
    count = np.zeros(total + horizon)
    for start in range(total):
        count[start:start + horizon] += 1
    uniq = np.array([np.mean(1.0 / count[s:s + horizon]) for s in range(total)])
    inner = uniq[horizon:-horizon]
    # `std() == 0` 으로 쓰면 안 된다 — 값이 전부 같아도 분산 계산에서 1e-17 이 남는다.
    # 하고 싶은 말은 "전부 같은 값" 이므로 그대로 쓴다.
    assert (inner == inner[0]).all(), "안쪽 고유도가 일정하지 않다"
    assert inner[0] == pytest.approx(1.0 / horizon)


# --------------------------------------------------------- 귀무 세계 만들기

def _outcomes():
    import pandas as pd
    frame = pd.DataFrame({
        "c_rsi": range(1200),
        "direction_hit": [float(v) for v in range(1200)],
        "realised": [float(v) for v in range(1200)],
    })
    return frame


def test_block_shuffle_moves_outcomes_and_leaves_conditions():
    """귀무 세계는 **결과만** 섞은 것이어야 한다. 조건까지 섞으면 다른 실험이 된다."""
    import pandas as pd

    frame = _outcomes()
    mixed = overfit.block_shuffle(frame, ("direction_hit", "realised"), 100, seed=1)

    pd.testing.assert_series_equal(mixed["c_rsi"], frame["c_rsi"])
    assert sorted(mixed["direction_hit"]) == sorted(frame["direction_hit"])
    assert not mixed["direction_hit"].equals(frame["direction_hit"]), "섞이지 않았다"


def test_block_shuffle_keeps_outcomes_on_the_same_row():
    """같이 움직이는 결과들은 **같은 순서로** 섞여야 한다.

    따로 섞으면 '방향은 맞고 밴드는 틀린' 판이 없던 조합으로 만들어진다.
    """
    frame = _outcomes()
    mixed = overfit.block_shuffle(frame, ("direction_hit", "realised"), 100, seed=2)
    assert (mixed["direction_hit"].to_numpy() == mixed["realised"].to_numpy()).all()


def test_block_shuffle_keeps_neighbours_together():
    """**덩어리째** 섞는다는 것이 이 함수의 존재 이유다.

    한 줄씩 섞으면 적중이 시간에 뭉쳐 다니는 성질이 사라져 귀무 세계가 실제보다
    깨끗해지고, 문턱이 너무 낮게 잡힌다. 실제로 답이 뒤집힌 적이 있다.
    """
    import numpy as np

    frame = _outcomes()
    mixed = overfit.block_shuffle(frame, ("direction_hit",), 100, seed=3)
    values = mixed["direction_hit"].to_numpy()
    # 덩어리 안에서는 값이 1씩 이어져야 한다. 대부분의 이웃이 그래야 한다.
    steps = np.diff(values)
    assert np.mean(steps == 1.0) > 0.9, "덩어리가 유지되지 않았다"


def test_block_shuffle_ignores_columns_that_are_not_there():
    """표마다 있는 결과 열이 다르다. 없는 열을 넘겨도 죽으면 안 된다."""
    frame = _outcomes()
    mixed = overfit.block_shuffle(frame, ("direction_hit", "없는열"), 100, seed=4)
    assert "없는열" not in mixed.columns


def test_pick_block_follows_the_autocorrelation():
    """뭉쳐 다니는 계열은 덩어리가 길고, 잡음은 짧다.

    손으로 고른 200 을 버린 이유다 — 실제 계열은 그보다 훨씬 길게 뭉쳐 있었다.
    """
    import numpy as np

    rng = np.random.default_rng(0)
    white = rng.standard_normal(20000)
    slow = np.convolve(rng.standard_normal(20300), np.ones(300) / 300, "valid")[:20000]
    assert overfit.pick_block(white) < overfit.pick_block(slow)


def test_the_nightly_path_does_not_need_arch(monkeypatch):
    """**야간 작업이 `arch` 없이 돌아야 한다.**

    Actions 의 `daily`·`recommend` 는 `.[ml]` 만 설치한다. `overfit` 을 모듈째
    무겁게 만들면 그 두 작업이 같이 죽는다 — `arch` 는 `pick_block` 안에서만 부른다.
    """
    import builtins
    import importlib

    import pandas as pd

    real = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.split(".")[0] == "arch":
            raise ImportError("arch 없음(모의)")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    module = importlib.reload(overfit)

    # 야간 작업이 실제로 쓰는 것들
    assert module.expected_max(30, 0.0015) > 0
    assert module.effective_n(30_000, 10) == 3_000
    frame = pd.DataFrame({"a": range(400), "b": [float(v) for v in range(400)]})
    assert len(module.block_shuffle(frame, ("b",), 50, 0)) == 400

    # 연구용 함수만 arch 를 요구한다
    with pytest.raises(ImportError):
        module.pick_block(frame["b"])
