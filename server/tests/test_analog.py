"""유사구간 검색과 예측 경로.

여기서 제일 중요한 건 정확도가 아니라 **미래를 안 보는 것**이다. 사례가 미래를 알면
예측이 예쁘게 맞고, 그 예쁜 숫자를 믿고 돈을 잃는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from marketlens.analog import matcher, projection
from tests.conftest import make_candles


def test_znorm_removes_level_and_scale():
    """가격 수준과 변동 폭이 달라도 같은 모양이면 같아야 한다."""
    base = np.array([1.0, 2.0, 3.0, 2.0, 1.0])
    scaled = base * 1000 + 50_000
    np.testing.assert_allclose(matcher.znorm(base), matcher.znorm(scaled), atol=1e-9)


def test_znorm_survives_a_flat_window():
    assert not np.isnan(matcher.znorm(np.full(5, 7.0))).any()


def test_dtw_is_zero_for_identical_series():
    a = np.array([0.0, 1.0, 2.0, 1.0, 0.0])
    assert matcher.dtw_distance(a, a) == pytest.approx(0.0)


def test_dtw_forgives_a_small_time_shift():
    """DTW 를 쓰는 이유. 한 칸 밀린 같은 모양은 유클리드보다 가깝게 나와야 한다."""
    a = np.array([0.0, 0.0, 1.0, 2.0, 1.0, 0.0, 0.0, 0.0])
    b = np.array([0.0, 0.0, 0.0, 1.0, 2.0, 1.0, 0.0, 0.0])
    euclid = float(np.sqrt(((a - b) ** 2).mean()))
    assert matcher.dtw_distance(a, b) < euclid


def test_matches_never_reach_into_the_future(candles):
    """사례의 결과 구간이 데이터 끝을 넘으면 안 된다.

    넘으면 아직 일어나지 않은 봉을 '과거 사례'로 쓰는 것이고, 그 순간 이 도구는
    거짓말을 하기 시작한다.
    """
    horizon = 24
    series = matcher.Series("t", candles)
    found = matcher.search(candles, [series], window=32, horizon=horizon, top_k=15)
    assert found
    for match in found:
        assert match.index + horizon <= len(candles) - 1
        assert len(match.path) == horizon + 1
        assert match.path[0] == pytest.approx(0.0)


def test_matches_do_not_overlap_each_other(candles):
    """이웃한 거의 같은 창이 스무 개 뽑히면 '사례 20건'이 거짓말이 된다."""
    window = 32
    found = matcher.search(candles, [matcher.Series("t", candles)],
                           window=window, horizon=12, top_k=20)
    positions = sorted(m.index for m in found)
    gaps = [b - a for a, b in zip(positions, positions[1:])]
    assert all(gap >= window // 2 for gap in gaps), gaps


def test_mask_restricts_candidates(candles):
    """조건부 검색. 마스크 밖 자리는 후보에 아예 못 들어와야 한다."""
    allowed = np.zeros(len(candles), dtype=bool)
    allowed[100:200] = True
    found = matcher.search(
        candles,
        [matcher.Series("t", candles, mask=allowed)],
        window=24, horizon=12, top_k=10,
    )
    assert found
    assert all(100 <= m.index < 200 for m in found)


def test_empty_mask_yields_no_matches(candles):
    found = matcher.search(
        candles,
        [matcher.Series("t", candles, mask=np.zeros(len(candles), dtype=bool))],
        window=24, horizon=12,
    )
    assert found == []


def test_short_history_returns_nothing():
    tiny = make_candles(count=20)
    assert matcher.search(tiny, [matcher.Series("t", tiny)], window=48, horizon=12) == []


def test_context_weight_changes_the_ranking(candles):
    """모양만 볼 때와 상황까지 볼 때의 답이 같으면 상황 축이 아무 일도 안 하는 것이다."""
    shape_only = matcher.search(candles, [matcher.Series("s", candles)],
                                window=32, horizon=12, top_k=10, context_weight=0.0)
    context_only = matcher.search(candles, [matcher.Series("c", candles)],
                                  window=32, horizon=12, top_k=10, context_weight=1.0)
    assert [m.index for m in shape_only] != [m.index for m in context_only]


# --- 예측 경로 -------------------------------------------------------------

def _matches(candles, horizon=12):
    return matcher.search(candles, [matcher.Series("t", candles)],
                          window=32, horizon=horizon, top_k=15)


def test_weighted_quantile_matches_plain_quantile_with_equal_weights():
    values = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    weights = np.ones(5)
    assert projection.weighted_quantile(values, weights, 0.5) == pytest.approx(3.0)


def test_projection_bands_are_ordered(candles):
    result = projection.project(candles, _matches(candles), 12, "1h")
    assert result["available"]
    for step in range(13):
        values = [result["bands"][f"p{q}"][step]["value"] for q in (10, 25, 50, 75, 90)]
        assert values == sorted(values), f"{step}번째 봉에서 밴드가 뒤집혔다: {values}"


def test_projection_starts_at_the_current_price(candles):
    result = projection.project(candles, _matches(candles), 12, "1h")
    last = float(candles["close"].iloc[-1])
    for path in result["paths"]:
        assert path["points"][0]["value"] == pytest.approx(last)
    assert result["bands"]["p50"][0]["value"] == pytest.approx(last, rel=1e-6)


def test_projection_times_go_into_the_future(candles):
    horizon = 12
    result = projection.project(candles, _matches(candles, horizon), horizon, "1h")
    last_second = int(candles["ts"].iloc[-1] // 1000)
    times = [p["time"] for p in result["bands"]["p50"]]
    assert times[0] == last_second
    assert times[-1] == last_second + 3600 * horizon
    assert times == sorted(times)


def test_projection_reports_when_it_should_not_be_trusted(candles):
    """사례가 세 건뿐이면 신뢰할 수 없다고 말해야 한다."""
    few = _matches(candles)[:3]
    result = projection.project(candles, few, 12, "1h")
    assert result["available"]
    assert result["diagnostics"]["reliable"] is False


def test_projection_without_matches_says_so(candles):
    result = projection.project(candles, [], 12, "1h")
    assert result["available"] is False
    assert result["reason"]


def test_projection_cites_its_evidence(candles):
    result = projection.project(candles, _matches(candles), 12, "1h")
    keys = {c["key"] for c in result["citations"]}
    # 기술적 분석의 예측력이 논쟁적이라는 사실을 숨기지 않는다.
    assert "technical_pattern_information" in keys
