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
