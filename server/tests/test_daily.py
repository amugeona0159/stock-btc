"""매일 자동 학습의 규칙.

여기가 무너지면 매일 잡음으로 모델이 바뀐다. 자동화 중에 제일 나쁜 종류다 —
아무도 안 보는 사이에 조용히 나빠지고, 로그는 매일 "승격"이라고 적힌다.
"""
from __future__ import annotations

import importlib.util
import json
import random
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    """`scripts/daily.py` 는 패키지가 아니라 스크립트라 경로로 읽어 온다."""
    spec = importlib.util.spec_from_file_location("daily", ROOT / "scripts" / "daily.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["daily"] = module
    spec.loader.exec_module(module)
    return module


daily = _load()


def _epoch(stamp: str) -> float:
    return datetime.fromisoformat(stamp).timestamp()


# --- 승격 규칙 -----------------------------------------------------------

def _promoted(champion: float, challenger: float | None) -> bool:
    """`run()` 안의 판정과 같은 식. 여기서 한 번 더 못 박아 둔다."""
    return challenger is not None and challenger > champion + daily.PROMOTE_MARGIN


@pytest.mark.parametrize("champion, challenger, expected", [
    (0.010, 0.0125, True),    # 마진을 넘겼다
    (0.010, 0.0120, False),   # 딱 마진 — 넘긴 게 아니다
    (0.010, 0.0119, False),   # 조금 나은 정도는 잡음이다
    (0.010, 0.010, False),
    (0.010, 0.000, False),
    (-0.050, -0.040, True),   # 둘 다 기준선을 못 넘어도 덜 나쁜 쪽으로는 간다
    (0.010, None, False),     # 도전자가 학습에 실패하면 챔피언 유지
])
def test_promotion_needs_the_margin(champion, challenger, expected):
    assert _promoted(champion, challenger) is expected


def test_the_margin_is_not_zero():
    """0 이면 동전던지기로 매일 모델이 바뀐다. 실수로 0 이 되는 걸 막는다."""
    assert daily.PROMOTE_MARGIN > 0


# --- 도전자 만들기 -------------------------------------------------------

def test_variant_changes_exactly_one_knob():
    """여러 개를 같이 바꾸면 뭐가 이겼는지 알 수 없다."""
    rng = random.Random(0)
    base = daily.Config()
    for _ in range(80):
        changed, label = daily.variant(base, "1d", rng)
        differing = [k for k in vars(base) if getattr(base, k) != getattr(changed, k)]
        assert len(differing) == 1, label
        assert differing[0] in label


def test_variant_keeps_horizon_inside_the_learnable_band():
    """지평은 잰 범위 안에서만 흔든다 — 밖은 어차피 기준선이 이긴다."""
    from marketlens.api.routes import LEARNABLE

    rng = random.Random(7)
    for timeframe, (lo, hi) in LEARNABLE.items():
        config = daily.Config(horizon=daily.horizon_options(timeframe)[0])
        for _ in range(40):
            changed, _ = daily.variant(config, timeframe, rng)
            assert lo <= changed.horizon <= hi


# --- 선택과 집중 ---------------------------------------------------------

def test_unseen_targets_go_first():
    target = daily.Target("binance", "BTCUSDT", "1d")
    assert daily.priority(target, None, 0.0) > 1.0


def test_better_targets_outrank_worse_ones_on_the_same_day():
    target = daily.Target("binance", "BTCUSDT", "1d")
    when = "2026-08-28T00:00:00+00:00"
    good = daily.Record(skill=0.02, updated=when)
    bad = daily.Record(skill=-0.05, updated=when)
    today = _epoch(when)
    assert daily.priority(target, good, today) > daily.priority(target, bad, today)


def test_staleness_eventually_revives_a_losing_target():
    """지는 자리도 버리지 않는다. 시장이 변하면 되는 자리가 뒤집히기 때문이다."""
    target = daily.Target("binance", "BTCUSDT", "1d")
    today = _epoch("2026-08-28T00:00:00+00:00")
    fresh_good = daily.Record(skill=0.02, updated="2026-08-28T00:00:00+00:00")
    stale_bad = daily.Record(skill=-0.05, updated="2026-01-01T00:00:00+00:00")
    assert daily.priority(target, stale_bad, today) > daily.priority(target, fresh_good, today)


# --- 기록 ----------------------------------------------------------------

def test_model_name_matches_what_the_api_serves():
    """이름이 어긋나면 매일 학습해도 화면은 옛 모델을 계속 쓴다."""
    from marketlens.api.routes import model_name

    for target in daily.targets():
        assert target.model == model_name(target.provider, target.symbol, target.timeframe)


def test_state_round_trips_through_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, "LEARNING", tmp_path)
    monkeypatch.setattr(daily, "CHAMPIONS", tmp_path / "champions.json")
    state = {"binance:BTCUSDT:1d": daily.Record(
        config=vars(daily.Config(horizon=20, window=72)), skill=0.0123,
        learned=True, rows=41000, symbols=["BTCUSDT"], updated=daily._now(),
        trials=14, promotions=3,
    )}
    daily.save_state(state)
    back = daily.load_state()
    assert back == state


def test_old_state_survives_a_new_knob(tmp_path, monkeypatch):
    """설정에 손잡이가 늘어도 어제 파일이 그대로 읽혀야 한다."""
    monkeypatch.setattr(daily, "CHAMPIONS", tmp_path / "champions.json")
    (tmp_path / "champions.json").write_text(json.dumps({"champions": {
        "binance:BTCUSDT:1d": {"config": {"window": 72, "지워진손잡이": 1},
                               "skill": 0.01, "낯선칸": True},
    }}, ensure_ascii=False), encoding="utf-8")
    back = daily.load_state()
    record = back["binance:BTCUSDT:1d"]
    assert record.config["window"] == 72
    assert record.config["horizon"] == daily.Config().horizon


def test_log_lines_are_one_json_object_each(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, "LEARNING", tmp_path)
    monkeypatch.setattr(daily, "LOG", tmp_path / "log.jsonl")
    for i in range(3):
        daily.append_log({"at": daily._now(), "target": f"t{i}", "promoted": False})
    lines = (tmp_path / "log.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3
    assert all(json.loads(line)["target"].startswith("t") for line in lines)


def test_summary_shows_the_trial_count(tmp_path, monkeypatch):
    """시험 횟수를 숨기면 '이겼다'를 곧이곧대로 읽게 된다."""
    monkeypatch.setattr(daily, "SUMMARY", tmp_path / "LEARNING.md")
    daily.write_summary(
        {"binance:BTCUSDT:1d": daily.Record(skill=0.011, learned=True, rows=41000,
                                            updated=daily._now(), trials=48, promotions=5)},
        [{"target": "binance:BTCUSDT:1d", "championSkill": 0.011,
          "challengerSkill": 0.009, "change": "window 48→72", "promoted": False}],
    )
    text = (tmp_path / "LEARNING.md").read_text(encoding="utf-8")
    assert "48" in text and "시험" in text
    assert "as-of" in text          # 승격에 안 쓴다는 사실이 요약에 남아 있어야 한다


def test_skill_reads_the_blended_number():
    """단독 성적이 아니라 **기준선과 섞은** 성적으로 판정한다."""
    report = {"horizon": 10, "skill": {"10": 0.9}, "blendSkill": {"10": 0.012}}
    assert daily.skill_of(report) == pytest.approx(0.012)
    assert daily.skill_of({"horizon": 10, "blendSkill": {}}) == 0.0
