"""학습층.

여기서 제일 중요한 건 정확도가 아니라 **정직함**이다. 학습이 아무것도 못 더했는데
더했다고 말하면, 그 위에서 내리는 모든 판단이 거짓이 된다.
"""
from __future__ import annotations

from pathlib import Path

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

def test_folds_only_ever_train_on_the_past():
    """검증 구간은 항상 학습 구간보다 뒤에 있어야 한다."""
    ts = np.arange(2000) * 3_600_000
    for train, test in ml.time_folds(ts, horizon_ms=10 * 3_600_000, count=4):
        assert ts[train].max() < ts[test].min()


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
    """합성 데이터 세 종목을 모아 실제로 학습한다. 느리므로 모듈에서 한 번만."""
    import marketlens.forecast.ml.model as module

    module.MODEL_DIR = tmp_path_factory.mktemp("models")
    datasets = [
        module.SymbolData(f"SYN{i}", make_candles(count=1400, seed=21 + i), [], None)
        for i in range(3)
    ]
    report = module.train(datasets, "unit-test", horizon=12, window=32, folds=3,
                          timeframe="1h")
    return datasets[0].df, report, module


def test_training_reports_all_three_baselines(trained):
    """모델 단독 · 섞은 결과 · 변동성 기준선. 셋을 다 보여야 무엇이 이겼는지 안다."""
    _, report, _ = trained
    h = str(report["horizon"])
    assert h in report["skill"] and h in report["blendSkill"] and h in report["volSkill"]
    assert report["rows"] > 400 and report["folds"] >= 2


def test_pooling_actually_pools(trained):
    """여러 종목이 한 표에 들어갔어야 한다. 하나만 들어가면 풀링이 안 된 것이다."""
    _, report, _ = trained
    assert len(report["symbols"]) == 3


def test_blend_is_never_worse_than_the_model_alone_by_much(trained):
    """섞기는 기준선 쪽으로 당기는 것이라, 모델 단독보다 크게 나쁠 수 없다."""
    _, report, _ = trained
    h = str(report["horizon"])
    assert report["blendSkill"][h] >= report["skill"][h] - 0.02


def test_blend_weight_is_a_fraction(trained):
    _, report, _ = trained
    for weight in report["weights"].values():
        assert 0.0 <= weight <= 1.0


def test_training_says_plainly_whether_it_learned(trained):
    """`learnedSomething` 과 문장이 서로 맞아야 한다. 여기가 어긋나면 화면이 거짓말을 한다."""
    _, report, _ = trained
    assert isinstance(report["learnedSomething"], bool)
    if report["learnedSomething"]:
        assert "넘었다" in report["verdict"] and "못 넘었다" not in report["verdict"]
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
    expected = "blend" if report["learnedSomething"] else "volatility-baseline"
    assert out["source"] == expected
    if expected == "blend":
        # 화면에 적힌 비중과 리포트의 비중이 어긋나면 사용자가 잘못 읽는다.
        assert out["weight"] == pytest.approx(report["weights"][str(report["horizon"])])


def test_missing_model_is_reported_not_crashed(trained):
    df, _, module = trained
    out = module.predict(df, "없는모델", [], "1h")
    assert out["available"] is False and "없다" in out["reason"]


def test_too_little_data_is_refused():
    with pytest.raises(ValueError):
        ml.train([ml.SymbolData("TINY", make_candles(count=200))], "tiny",
                 horizon=12, window=32)


def test_time_folds_split_by_time_not_row():
    """여러 종목을 섞으면 같은 시각의 다른 종목이 학습과 검증에 나뉘면 안 된다."""
    ts = np.repeat(np.arange(1000) * 86_400_000, 3)   # 세 종목이 같은 날짜를 공유
    folds = ml.time_folds(ts, horizon_ms=10 * 86_400_000, count=3)
    assert folds
    for train, test in folds:
        assert ts[train].max() < ts[test].min()
        # 경계에서 지평만큼 비어 있어야 한다.
        assert ts[test].min() - ts[train].max() >= 10 * 86_400_000


def test_attention_columns_never_drop_a_symbol():
    """위키백과 문서가 없는 종목도 학습 표에 남아야 한다."""
    df = make_candles(count=300)
    frame = dataset.attention_columns(df, None)
    assert frame.notna().all().all()
    assert (frame["attention_available"] == -1.0).all()


def test_prob_up_reads_the_quantile_curve():
    # 전부 양수면 확실한 상승, 전부 음수면 확실한 하락.
    assert ml._prob_up({0.1: 0.01, 0.5: 0.02, 0.9: 0.03}) == 1.0
    assert ml._prob_up({0.1: -0.03, 0.5: -0.02, 0.9: -0.01}) == 0.0
    # 중앙값이 0이면 절반.
    assert ml._prob_up({0.1: -0.02, 0.5: 0.0, 0.9: 0.02}) == pytest.approx(0.5, abs=0.01)


# --- 배포판이 기록을 읽을 수 있나 ---------------------------------------

ROOT = Path(__file__).resolve().parents[2]


def test_the_image_ships_the_learning_records():
    """**`api/learning.py`·`api/recommend.py` 는 저장소 뿌리의 `learning/` 을 읽는다.**

    `MARKET_LENS_LEARNING` 은 스크립트용이고 서버는 학습을 안 돌린다. 그래서 이미지에
    `learning/` 이 안 들어가면 배포판에서 아침 추천·챔피언·성적이 통째로 비는데,
    화면은 "아직 없다"고만 말해서 원인이 안 보인다.
    """
    from marketlens.api.learning import DIRS

    assert DIRS[-1].name == "learning", "읽는 자리가 바뀌었으면 Dockerfile 도 같이 본다"
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY learning/" in docker, "이미지에 학습 기록이 안 들어간다"


def test_the_build_context_drops_the_heavy_things():
    """`flyctl deploy --remote-only` 는 컨텍스트를 통째로 올린다. 빼 두지 않으면
    `.venv`·모델·개인 기록까지 매 배포마다 올라간다."""
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for heavy in (".venv/", "node_modules/", "store_data/", "alerts/",
                  "learning-local/", "learning/study/verdicts.jsonl"):
        assert heavy in ignore, f".dockerignore 에 {heavy} 가 없다"
    # 화면이 읽는 것은 남아야 한다 — 통째로 빼면 위 테스트가 무의미해진다.
    assert "learning/" not in ignore


def test_a_new_record_reaches_the_deployment():
    """밤새 배운 것과 아침 추천이 이미지에 들어가는데, 배포 트리거가 그 경로를 안 보면
    영영 안 닿는다. 코드가 안 바뀌는 날이 대부분이라 더 그렇다."""
    deploy = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert '"learning/**"' in deploy, "학습 기록이 바뀌어도 배포가 안 돈다"
