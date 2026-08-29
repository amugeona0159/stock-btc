"""메타 라벨링 실험 장치가 미래를 안 보는지.

**이 파일이 지키는 건 결론이 아니라 전제다.** 축에 결과가 한 칸이라도 섞이면
메타 모델이 답을 보고 배우고, 그러면 p 값이 무슨 값이 나오든 뜻이 없다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import metalabel  # noqa: E402


def _frame() -> pd.DataFrame:
    return pd.DataFrame({
        "origin_ts": [1, 2, 3, 4],
        "predicted": [0.1, -0.2, 0.3, -0.4],
        "direction_hit": [1.0, 0.0, 1.0, 0.0],
        "band_hit": [1.0, 1.0, 0.0, 1.0],
        "realised": [0.2, -0.1, 0.4, -0.5],
        "error_atr": [0.5, 0.6, 0.7, 0.8],
        "baseline_error_atr": [0.5, 0.6, 0.7, 0.8],
        "moved": [True, True, False, True],
        "c_rsi": [0.3, 0.4, 0.5, 0.6],
        "c_move_atr": [0.01, -0.02, 0.03, -0.04],
    })


def test_features_take_only_conditions():
    """`c_` 로 시작하는 조건 축만 쓴다."""
    assert metalabel.features(_frame()) == ["c_move_atr", "c_rsi"]


def test_no_outcome_column_can_become_a_feature():
    """결과 열이 축으로 새면 그 실험은 통째로 무의미하다."""
    columns = metalabel.features(_frame())
    for outcome in metalabel.OUTCOMES:
        assert outcome not in columns
    assert "predicted" not in columns


def test_a_mostly_empty_condition_is_dropped():
    """절반 넘게 비어 있는 축은 뺀다. 학습 표가 그 축 때문에 통째로 줄어든다."""
    frame = _frame()
    frame["c_sparse"] = [1.0, None, None, None]
    assert "c_sparse" not in metalabel.features(frame)
