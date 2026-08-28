"""기권 규칙.

학습이 "이 조건에서는 틀린다"고 잰 자리에서 방향을 말하지 않는 장치다. 여기가
무너지면 두 가지 중 하나가 일어난다 — 아무 데서나 입을 다물거나(쓸모가 사라진다),
아무 말이나 하거나(잰 게 소용없어진다).
"""
from __future__ import annotations

import json

import pytest

from marketlens.forecast import gate


@pytest.fixture
def gated(tmp_path, monkeypatch):
    """규칙 파일을 심어 준다. 실제 경로는 저장소 뿌리 기준이라 갈아끼운다."""
    def install(payload: dict) -> None:
        (tmp_path / "gate.json").write_text(json.dumps(payload, ensure_ascii=False),
                                            encoding="utf-8")
        monkeypatch.setattr(gate, "DIRS", (tmp_path,))
    return install


RULE = {
    "rule": {"condition": "analog_corr_max", "op": ">", "threshold": 0.95},
    "label": "analog_corr_max > 0.95 이면 기권",
    "holdout": {"withoutRule": 0.616, "withRule": 0.642, "n": 173},
    "holdoutLooks": 4,
    "trials": 29,
}


def test_no_file_means_no_abstaining():
    """규칙이 없으면 아무 데서도 입을 다물지 않는다. 이게 기본값이어야 한다."""
    gate.DIRS = tuple()
    assert gate.rule() is None
    assert gate.abstains({"analog_corr_max": 0.99}) == (False, "")


def test_the_rule_fires_above_the_threshold(gated):
    gated(RULE)
    quiet, why = gate.abstains({"analog_corr_max": 0.99})
    assert quiet is True
    assert "방향을 말하지 않는다" in why
    # 성적을 같이 말한다. 규칙만 보여주면 그냥 마법 규칙이 된다.
    assert "61.6%" in why and "64.2%" in why


def test_the_rule_stays_quiet_below_the_threshold(gated):
    gated(RULE)
    assert gate.abstains({"analog_corr_max": 0.5}) == (False, "")


def test_a_missing_condition_never_abstains(gated):
    """모르는 조건으로 입을 다무는 건 규칙이 아니라 사고다."""
    gated(RULE)
    assert gate.abstains({}) == (False, "")
    assert gate.abstains({"analog_corr_max": None}) == (False, "")
    assert gate.abstains({"analog_corr_max": "많음"}) == (False, "")


def test_a_rule_that_lost_on_the_holdout_is_not_used(gated):
    """최종 구간에서 못 이긴 규칙은 `scripts/study.py` 가 애초에 안 내보낸다.
    그래도 그런 파일이 오면 여기서 한 번 더 막는다."""
    gated({"rule": None, "reason": "최종 구간에서 못 이겼다"})
    assert gate.rule() is None
    assert gate.abstains({"analog_corr_max": 0.99}) == (False, "")


def test_status_carries_the_evidence(gated):
    """규칙과 함께 **몇 번 시험했고 최종 구간을 몇 번 열어 봤는지**가 같이 나가야 한다."""
    gated(RULE)
    found = gate.status()
    assert found["available"] is True
    assert found["trials"] == 29
    assert found["holdoutLooks"] == 4
    assert found["holdout"]["withRule"] > found["holdout"]["withoutRule"]


def test_a_broken_file_does_not_break_the_forecast(tmp_path, monkeypatch):
    """규칙 파일이 깨졌다고 예측 전체가 막히면 안 된다. 기권은 부가 장치다."""
    (tmp_path / "gate.json").write_text("{ 깨짐", encoding="utf-8")
    monkeypatch.setattr(gate, "DIRS", (tmp_path,))
    assert gate.rule() is None
    assert gate.abstains({"analog_corr_max": 0.99}) == (False, "")


def test_the_local_folder_wins(tmp_path, monkeypatch):
    """이 PC 가 잰 게 있으면 그쪽이 맞다 — 저장소 것은 Actions 가 쓴 것이다."""
    local, repo = tmp_path / "local", tmp_path / "repo"
    local.mkdir()
    repo.mkdir()
    (local / "gate.json").write_text(json.dumps({
        "rule": {"condition": "rsi", "op": "<", "threshold": 0.1}}), encoding="utf-8")
    (repo / "gate.json").write_text(json.dumps({
        "rule": {"condition": "adx", "op": ">", "threshold": 0.9}}), encoding="utf-8")
    monkeypatch.setattr(gate, "DIRS", (local, repo))
    assert gate.rule()["condition"] == "rsi"


def test_thresholds_are_not_written_by_hand():
    """임계값을 코드에 적으면 그건 측정이 아니라 믿음이다.

    이 모듈에는 읽고 적용하는 코드만 있어야 한다 — 숫자 리터럴이 조건으로 쓰이면
    다음 사람이 '여기 하나쯤이야' 하며 손으로 규칙을 심게 된다.
    """
    import inspect

    source = inspect.getsource(gate)
    body = "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith("#"))
    assert "0." not in body.split('"""')[-1], "규칙 임계값이 코드에 박혀 있다"
