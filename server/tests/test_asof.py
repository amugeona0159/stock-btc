"""as-of 예측의 미래 차단.

"그 시점에 서서 예측한다"는 말이 참이려면, **origin 뒤의 데이터를 아무리 바꿔도
그 시점의 예측이 변하지 않아야** 한다. 변한다면 어딘가로 미래가 새고 있는 것이고,
그 위에서 잰 정확도는 전부 거짓이다.

여기서 자르는 곳은 넷이다 — 시세 · 사건 · 유사구간 후보 · 학습 표.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from marketlens.analog import matcher, projection
from marketlens.core.candle import to_frame
from marketlens.events.schema import Event
from marketlens.forecast.ml import dataset
from tests.conftest import make_candles

ORIGIN = 700


def _tampered(df: pd.DataFrame, at: int = ORIGIN) -> pd.DataFrame:
    """origin 뒤를 크게 흔든 사본. 그 시점 예측은 이걸 몰라야 한다."""
    rows = df.to_dict("records")
    for i in range(at + 1, len(rows)):
        for key in ("open", "high", "low", "close"):
            rows[i][key] *= 3.0
        rows[i]["volume"] *= 50.0
    return to_frame(rows)


@pytest.fixture(scope="module")
def pair():
    clean = make_candles(count=900, seed=33)
    return clean, _tampered(clean)


def _view(df: pd.DataFrame, origin: int = ORIGIN) -> pd.DataFrame:
    return df.iloc[: origin + 1].reset_index(drop=True)


def test_the_tamper_is_actually_visible(pair):
    """대조군 검사 — 흔든 게 실제로 달라야 이 파일의 나머지가 의미가 있다."""
    clean, dirty = pair
    assert clean["close"].iloc[-1] != pytest.approx(dirty["close"].iloc[-1])
    assert _view(clean).equals(_view(dirty))


def test_analog_search_ignores_the_future(pair):
    """유사구간 후보는 origin 이전에서만 나와야 한다."""
    clean, dirty = pair
    found = []
    for df in (clean, dirty):
        view = _view(df)
        found.append(matcher.search(view, [matcher.Series("t", view)],
                                    window=32, horizon=12, top_k=10))
    a, b = found
    assert a and len(a) == len(b)
    assert [m.index for m in a] == [m.index for m in b]
    assert [round(m.distance, 9) for m in a] == [round(m.distance, 9) for m in b]


def test_projection_ignores_the_future(pair):
    clean, dirty = pair
    bands = []
    for df in (clean, dirty):
        view = _view(df)
        matches = matcher.search(view, [matcher.Series("t", view)],
                                 window=32, horizon=12, top_k=10)
        bands.append(projection.project(view, matches, 12, "1h")["bands"]["p50"])
    for left, right in zip(*bands):
        assert left["value"] == pytest.approx(right["value"], rel=1e-12)


def test_feature_panel_ignores_the_future(pair):
    """학습 표의 마지막 행은 origin 이후를 몰라야 한다."""
    clean, dirty = pair
    frames = [dataset.build(_view(df), [], window=32, horizon=12) for df in (clean, dirty)]
    a, b = (f[list(dataset.FEATURE_COLUMNS)].tail(1) for f in frames)
    pd.testing.assert_frame_equal(a, b, atol=1e-12)


def test_analog_features_ignore_the_future(pair):
    clean, dirty = pair
    a, b = (dataset.analog_features(_view(df), window=32, horizon=12, k=10)
            for df in (clean, dirty))
    pd.testing.assert_frame_equal(a, b, atol=1e-12)


def test_event_features_ignore_events_after_the_origin(pair):
    """origin 뒤의 사건을 넘겨줘도 그 시점 값이 변하면 안 된다.

    잘라 넣는 건 호출부의 책임이지만, 안 잘렸을 때 조용히 오염되는 대신
    여기서 드러나야 한다.
    """
    clean, _ = pair
    view = _view(clean)
    past = [Event(ts=int(clean["ts"].iloc[300]), kind="chart", title="과거",
                  source="detector", severity=0.8)]
    future = past + [Event(ts=int(clean["ts"].iloc[850]), kind="chart", title="미래",
                           source="detector", severity=1.0)]
    a = dataset.event_features(view, past)
    b = dataset.event_features(view, future)
    pd.testing.assert_frame_equal(a, b, atol=1e-12)


def test_attention_only_fills_forward():
    """관심도는 앞으로만 채운다. 뒤에서 당겨 오면 아직 안 나온 조회수를 쓰는 것이다."""
    from marketlens.events.sources import attention

    df = make_candles(count=200, timeframe="1d")
    day = int(df["ts"].iloc[100])
    views = pd.DataFrame({"ts": [day], "views": [5000.0]})
    series = attention._to_bars(views, df["ts"].to_numpy())
    assert series.iloc[99] != series.iloc[99] or np.isnan(series.iloc[99])  # 그 전은 비어 있다
    assert series.iloc[100] == 5000.0
    assert series.iloc[150] == 5000.0                                       # 뒤로는 채운다


def test_market_features_ignore_the_future(pair):
    """시장 요인도 인과적이어야 한다 — 시장이 앞으로 갈 길을 피처에 넣으면 안 된다."""
    from marketlens.forecast.ml import market

    clean, dirty = pair
    other = make_candles(count=900, seed=34)
    results = []
    for df in (clean, dirty):
        view = _view(df)
        series = market.market_series({"a": view, "b": _view(other)})
        results.append(market.features(view, series).tail(1))
    pd.testing.assert_frame_equal(results[0], results[1], atol=1e-12)


def test_forward_market_is_only_for_labels():
    """`market.forward` 는 미래를 담는다. 라벨 말고 다른 데 쓰이면 안 된다 —
    피처 목록에 이 이름이 새어 들어가지 않았는지 확인한다."""
    from marketlens.forecast.ml import market

    df = make_candles(count=300)
    series = market.market_series({"a": df, "b": make_candles(count=300, seed=9)})
    forward = market.forward(series, df, horizon=10)
    assert forward.tail(10).isna().all(), "마지막 지평만큼은 답을 모르는 게 정상이다"
    assert not any("forward" in name for name in dataset.FEATURE_COLUMNS)
