"""화면으로 나가는 지표 시리즈.

여기서 지키는 건 하나다 — **값이 없는 자리를 버리지 않는다.**

버리면 RSI(14) 의 첫 점이 캔들 14번째가 되고, lightweight-charts 의 logical 인덱스는
"그 차트의 첫 데이터" 기준이라 메인 차트의 0번과 보조 패널의 0번이 14봉(MACD 는 34봉)
어긋난다. 그 상태로 같은 범위를 넘기면 보조 패널이 다른 구간을 보여 준다 —
차트를 확대해도 RSI·MACD 가 안 맞던 이유가 정확히 이것이다.
"""
from __future__ import annotations

import numpy as np

from marketlens.core.series import _points, compute_requests
from marketlens.core.series import IndicatorRequest
from tests.conftest import make_candles


def test_a_missing_value_keeps_its_slot():
    """값이 없어도 자리는 남는다. 시각만 보낸다(whitespace)."""
    ts = np.arange(5, dtype="int64")
    found = _points(ts, np.array([np.nan, np.nan, 1.0, 2.0, np.nan]))
    assert [p["time"] for p in found] == [0, 1, 2, 3, 4]
    assert "value" not in found[0] and "value" not in found[1]
    assert found[2]["value"] == 1.0
    assert "value" not in found[4]


def test_nothing_at_all_is_an_empty_list():
    """하나도 없으면 빈 배열. 화면이 이걸 보고 시리즈를 아예 안 만든다 —
    없는 지표에 빈 패널이 생기면 안 된다."""
    assert _points(np.arange(3, dtype="int64"), np.full(3, np.nan)) == []


def test_every_output_covers_every_bar():
    """봉이 200개면 RSI 도 MACD 도 200점이다. 이게 어긋나면 패널이 안 맞는다."""
    df = make_candles(count=200, timeframe="1d")
    results = compute_requests(df, [
        IndicatorRequest(id="rsi", key="rsi", params={}),
        IndicatorRequest(id="macd", key="macd", params={}),
    ], "1d")
    assert results, "지표가 하나도 안 나왔다"
    for result in results:
        assert not result.get("error"), result.get("error")
        for output in result["outputs"]:
            if not output["data"]:
                continue                      # 전부 비어 있는 출력은 아예 안 보낸다
            assert len(output["data"]) == len(df), f"{result['key']}.{output['key']}"
            assert output["data"][0]["time"] == int(df["ts"].iloc[0]) // 1000


def test_warm_up_slots_have_no_value():
    """RSI 는 앞쪽이 비어 있어야 정상이다. 0 으로 채우면 없던 값이 생긴다."""
    df = make_candles(count=200, timeframe="1d")
    results = compute_requests(df, [IndicatorRequest(id="rsi", key="rsi", params={})], "1d")
    data = results[0]["outputs"][0]["data"]
    assert "value" not in data[0]
    assert any("value" in point for point in data), "값이 하나도 안 들어왔다"


def test_a_shifted_output_still_lines_up_on_the_grid():
    """일목 선행스팬은 마지막 봉 **뒤로** 나간다. 그건 앞을 채우는 것과 다른 규칙이라
    여기서 길이를 강요하면 안 된다 — 시각이 격자 위에 있는지만 본다."""
    from marketlens.core.timeframe import to_ms

    df = make_candles(count=300, timeframe="1d")
    results = compute_requests(df, [
        IndicatorRequest(id="ichimoku", key="ichimoku", params={}),
    ], "1d")
    step = to_ms("1d") // 1000
    for output in results[0]["outputs"]:
        for point in output["data"]:
            assert point["time"] % step == 0, f"{output['key']} 이 격자를 벗어났다"
