"""학습층.

여기서 제일 중요한 건 정확도가 아니라 **정직함**이다. 학습이 아무것도 못 더했는데
더했다고 말하면, 그 위에서 내리는 모든 판단이 거짓이 된다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from marketlens.events.schema import Event
from marketlens.forecast.ml import dataset
from marketlens.forecast.ml import model as ml
from tests.conftest import make_candles


# --- 학습 표 ---------------------------------------------------------------

def test_analog_features_never_use_the_future():
    """봉 i 의 유사구간 요약은 i 까지의 정보만 써야 한다.

    뒤쪽 데이터를 잘라 내도 앞쪽 값이 그대로여야 한다. 달라진다면 그 값은
    미래를 담고 있었던 것이고, 그 위에서 잰 성능은 전부 거짓이다.
    """
    df = make_candles(count=900, seed=5)
    full = dataset.analog_features(df, window=32, horizon=12, k=10)
    early = dataset.analog_features(df.iloc[:600].reset_index(drop=True),
                                    window=32, horizon=12, k=10)
    common = early.dropna().index
    assert len(common) > 50
    pd.testing.assert_frame_equal(
        full.loc[common], early.loc[common], check_names=False, atol=1e-9
    )


def test_analog_features_stay_bounded():
    df = make_candles(count=800, seed=6)
    frame = dataset.analog_features(df, window=32, horizon=12, k=10).dropna()
    assert not frame.empty
    for column in dataset.ANALOG_COLUMNS:
        assert frame[column].abs().max() <= 1.5, column


def test_event_features_do_not_look_ahead():
    """사건이 나기 전 봉의 사건 피처는 그 사건을 몰라야 한다."""
    df = make_candles(count=400)
    at = 300
    events = [Event(ts=int(df["ts"].iloc[at]), kind="chart", title="사건",
                    source="detector", severity=0.9)]
    frame = dataset.event_features(df, events)
    assert frame["event_severity"].iloc[at - 1] == 0.0
    assert frame["event_severity"].iloc[at] > 0.0
    # 시간이 지나면 옅어진다.
    assert frame["event_severity"].iloc[at + 30] < frame["event_severity"].iloc[at]


def test_panel_matches_the_candles_row_for_row():
    """피처 표와 캔들의 행이 어긋나면 라벨이 엉뚱한 봉에 붙는다."""
    df = make_candles(count=600)
    panel = dataset.build(df, [], window=48, horizon=24)
    assert len(panel) == len(df)
    assert panel["ts"].tolist() == df["ts"].tolist()


def test_forward_return_leaves_the_tail_unknown():
    df = make_candles(count=200)
    y = dataset.forward_return(df, 10)
    assert y.tail(10).isna().all()
    assert np.isfinite(y.iloc[-11])


# --- 검증 장치 --------------------------------------------------------------

def test_purged_folds_leave_a_gap():
    """학습 끝과 검증 시작 사이에 지평만큼 틈이 있어야 한다."""
    folds = ml.purged_folds(1000, horizon=24, count=4)
    assert folds
    for fold in folds:
        assert fold.test_start - fold.train_end == 24
        assert fold.train_end < fold.test_start < fold.test_end


def test_folds_only_ever_train_on_the_past():
    for fold in ml.purged_folds(2000, horizon=10, count=4):
        assert fold.train_end <= fold.test_start


def test_pinball_is_zero_for_a_perfect_prediction():
    y = np.array([1.0, 2.0, 3.0])
    assert ml.pinball(y, y, 0.5) == pytest.approx(0.0)


def test_pinball_penalises_the_right_side():
    """0.9분위 예측은 낮게 잡는 쪽이 더 아프게 벌받아야 한다."""
    y = np.array([1.0])
    too_low = ml.pinball(y, np.array([0.0]), 0.9)
    too_high = ml.pinball(y, np.array([2.0]), 0.9)
    assert too_low > too_high


def test_horizon_steps_end_at_the_target():
    steps = ml.horizon_steps(24)
    assert steps[-1] == 24 and len(steps) == 4 and steps == sorted(steps)


def test_volatility_scale_is_dimensionless():
    """가격을 1000배 해도 변동성 자는 그대로여야 한다 — 그래야 목표값이 무차원이 된다."""
    df = make_candles(count=300)
    scaled = df.copy()
    for column in ("open", "high", "low", "close"):
        scaled[column] = scaled[column] * 1000.0
    a = ml.volatility_scale(df).iloc[-1]
    b = ml.volatility_scale(scaled).iloc[-1]
    assert a == pytest.approx(b, rel=1e-9)


# --- 학습 전체 흐름 ----------------------------------------------------------

@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    """합성 데이터로 실제 학습을 한 번 돌린다. 느리므로 모듈에서 한 번만."""
    import marketlens.forecast.ml.model as module

    module.MODEL_DIR = tmp_path_factory.mktemp("models")
    df = make_candles(count=1600, seed=21)
    report = module.train(df, "unit-test", [], horizon=12, window=32, folds=3)
    return df, report, module


def test_training_reports_both_baselines(trained):
    _, report, _ = trained
    h = str(report["horizon"])
    assert h in report["skill"] and h in report["volSkill"]
    assert report["rows"] > 400 and report["folds"] >= 2


def test_training_says_plainly_whether_it_learned(trained):
    """`learnedSomething` 과 문장이 서로 맞아야 한다. 여기가 어긋나면 화면이 거짓말을 한다."""
    _, report, _ = trained
    assert isinstance(report["learnedSomething"], bool)
    if report["learnedSomething"]:
        assert "넘었다" in report["verdict"]
    else:
        assert "못 넘었다" in report["verdict"]


def test_band_coverage_is_calibrated(trained):
    """컨포멀 보정을 했으면 80% 밴드가 실제로 80%쯤 덮어야 한다."""
    _, report, _ = trained
    h = str(report["horizon"])
    assert report["coverage"][f"{h}:80"] == pytest.approx(0.8, abs=0.05)


def test_prediction_band_is_ordered_and_starts_at_price(trained):
    df, report, module = trained
    out = module.predict(df, "unit-test", [], "1h")
    assert out["available"]
    last = out["last"]
    for step in (0, report["horizon"]):
        values = [out["bands"][f"p{q}"][step]["value"] for q in (10, 25, 50, 75, 90)]
        assert values == sorted(values), f"{step}번째에서 밴드가 뒤집혔다"
    for band in out["bands"].values():
        assert band[0]["value"] == pytest.approx(last, rel=1e-9)
        assert band[0]["time"] < band[-1]["time"]


def test_prediction_says_which_source_it_used(trained):
    """기준선을 쓰면서 '학습 모델' 이라고 하면 안 된다."""
    df, report, module = trained
    out = module.predict(df, "unit-test", [], "1h")
    expected = "model" if report["learnedSomething"] else "volatility-baseline"
    assert out["source"] == expected


def test_missing_model_is_reported_not_crashed(trained):
    df, _, module = trained
    out = module.predict(df, "없는모델", [], "1h")
    assert out["available"] is False and "없다" in out["reason"]


def test_too_little_data_is_refused():
    with pytest.raises(ValueError):
        ml.train(make_candles(count=200), "tiny", [], horizon=12, window=32)


def test_prob_up_reads_the_quantile_curve():
    # 전부 양수면 확실한 상승, 전부 음수면 확실한 하락.
    assert ml._prob_up({0.1: 0.01, 0.5: 0.02, 0.9: 0.03}) == 1.0
    assert ml._prob_up({0.1: -0.03, 0.5: -0.02, 0.9: -0.01}) == 0.0
    # 중앙값이 0이면 절반.
    assert ml._prob_up({0.1: -0.02, 0.5: 0.0, 0.9: 0.02}) == pytest.approx(0.5, abs=0.01)
