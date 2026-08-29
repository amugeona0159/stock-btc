"""아침 매수 추천.

추천은 만들기 쉽고 자기를 속이기 쉽다. 여기서 지키는 것 넷:

1. **하루 한 번, 그날은 안 바뀐다** — 눌러서 바뀌면 추천이 아니다
2. **기준선은 후보 전체 평균이다** — 0 과 견주면 고르는 실력이 아니라 시장을 잰다
3. **모델을 안 쓴 날은 그 사실이 화면까지 간다** — 그때 순위는 사실상 변동성 순서다
4. **날짜는 KST 다** — 07:30 KST 는 22:30 UTC(전날)라, UTC 로 지으면 하루 어긋난다
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("recommend_script",
                                                  ROOT / "scripts" / "recommend.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["recommend_script"] = module
    spec.loader.exec_module(module)
    return module


script = _load()


# --- 날짜 ---------------------------------------------------------------

def test_the_date_is_the_korean_calendar_day():
    """07:30 KST = 22:30 UTC 전날. UTC 로 지으면 '오늘의 추천'에 어제가 뜬다."""
    import pandas as pd

    assert script.CALENDAR == "Asia/Seoul"
    kst = pd.Timestamp.now(tz="Asia/Seoul").strftime("%Y-%m-%d")
    assert script.today() == kst


# --- 확신도 -------------------------------------------------------------

def test_confidence_is_a_label_not_an_ordering():
    """`move_atr` 로 다시 줄 세우면 이 저장소가 이미 빠진 함정을 되살린다 —
    크게 이긴 규칙이 알고 보니 음수 예측을 통째로 버리는 상승장 편향이었다."""
    labels = script.confidence([0.01, 0.5, 2.0, -3.0, 0.02, 1.0])
    assert set(labels) <= {"low", "mid", "high"}
    assert labels[0] == "low" and labels[3] == "high"      # 부호가 아니라 크기로 가른다


def test_confidence_falls_back_when_there_is_too_little():
    assert script.confidence([None, 0.1]) == ["mid", "mid"]


# --- 채점 ---------------------------------------------------------------

def _frozen(tmp_path, symbols, buy, avoid, last_ts=1_000_000_000_000):
    body = {
        "provider": "fake", "lastTs": last_ts, "basedOn": "2026-08-20",
        "candidates": [{"symbol": s, "byDay": {"1": {"expected": 0.0,
                                                     "band": [-1.0, 1.0]}}}
                       for s in symbols],
        "buy": [{"symbol": s} for s in buy],
        "avoid": [{"symbol": s} for s in avoid],
    }
    frozen = {"date": "2026-08-20", "providers": {"fake": body}}
    (tmp_path / "2026-08-20.json").write_text(json.dumps(frozen, ensure_ascii=False),
                                              encoding="utf-8")
    return frozen, body


def test_the_baseline_is_all_candidates_not_the_rest(tmp_path, monkeypatch):
    """**추천을 뺀 나머지와 견주면 안 된다.** 그러면 같은 표본을 두 조각으로 나눠
    비교하는 셈이라 숫자의 뜻이 달라진다. 기준선은 '후보를 전부 똑같이 샀다면' 이다.
    """
    import asyncio

    import numpy as np
    import pandas as pd

    symbols = [f"S{i}" for i in range(6)]
    returns = {"S0": 3.0, "S1": 2.0, "S2": 1.0, "S3": 0.0, "S4": -1.0, "S5": -2.0}
    frozen, body = _frozen(tmp_path, symbols, ["S0", "S1", "S2"], ["S4", "S5"])

    class FakeProvider:
        async def history(self, symbol, timeframe, limit):
            base = 100.0
            after = base * (1 + returns[symbol] / 100)
            return pd.DataFrame({
                "ts": [1_000_000_000_000, 1_086_400_000_000],
                "open": [base, base], "high": [base, after], "low": [base, after],
                "close": [base, after], "volume": [1.0, 1.0], "closed": [True, True],
            })

    monkeypatch.setattr(script, "get_provider", lambda key: FakeProvider())
    row = asyncio.run(script.score_one(frozen, "fake", body, 1))
    assert row is not None
    assert row["buyPct"] == pytest.approx(2.0)                 # (3+2+1)/3
    # 후보 여섯 전부의 평균 — 추천 자신도 들어간다
    assert row["universePct"] == pytest.approx(float(np.mean(list(returns.values()))))
    assert row["edgePct"] == pytest.approx(2.0 - 0.5)
    assert row["avoidPct"] == pytest.approx(-1.5)


def test_scoring_waits_when_the_horizon_has_not_passed(tmp_path, monkeypatch):
    """아직 결과가 안 나온 건 **실패가 아니라 기다림**이다. 건너뛰고 내일 다시."""
    import asyncio

    import pandas as pd

    frozen, body = _frozen(tmp_path, [f"S{i}" for i in range(6)],
                           ["S0", "S1", "S2"], ["S4", "S5"])

    class Short:
        async def history(self, symbol, timeframe, limit):
            return pd.DataFrame({
                "ts": [1_000_000_000_000], "open": [1.0], "high": [1.0],
                "low": [1.0], "close": [1.0], "volume": [1.0], "closed": [True],
            })

    monkeypatch.setattr(script, "get_provider", lambda key: Short())
    assert asyncio.run(script.score_one(frozen, "fake", body, 1)) is None


def test_scoring_anchors_on_the_bar_not_the_date(tmp_path, monkeypatch):
    """`lastTs` 가 예측이 딛고 선 마지막 확정봉이다. 날짜로 맞추면 공휴일 하나에
    조용히 밀린다 — 여기서는 파일 날짜(`basedOn`)를 일부러 엉뚱하게 두고,
    그래도 `lastTs` 봉에서 재는지 본다."""
    import asyncio

    import pandas as pd

    symbols = [f"S{i}" for i in range(6)]
    # 두 번째 봉을 앵커로 준다. 날짜를 봤다면 첫 봉에서 재게 된다.
    anchor = 1_086_400_000_000
    frozen, body = _frozen(tmp_path, symbols, ["S0", "S1", "S2"], ["S4", "S5"],
                           last_ts=anchor)
    body["basedOn"] = "1999-01-01"                 # 일부러 틀린 날짜

    class Three:
        async def history(self, symbol, timeframe, limit):
            return pd.DataFrame({
                "ts": [1_000_000_000_000, anchor, 1_172_800_000_000],
                "open": [100.0, 100.0, 100.0], "high": [100.0, 100.0, 110.0],
                "low": [100.0, 100.0, 110.0], "close": [50.0, 100.0, 110.0],
                "volume": [1.0, 1.0, 1.0], "closed": [True, True, True],
            })

    monkeypatch.setattr(script, "get_provider", lambda key: Three())
    row = asyncio.run(script.score_one(frozen, "fake", body, 1))
    # 앵커 봉(100) → 다음 봉(110) = +10%. 첫 봉(50)에서 쟀다면 +100% 가 나온다.
    assert row is not None and row["buyPct"] == pytest.approx(10.0)


def test_scoring_is_idempotent(tmp_path, monkeypatch):
    """재실행이 정상인 스크립트다. 중복을 안 막으면 표본이 두 배가 되고
    모든 비율이 조용히 좋아진다."""
    monkeypatch.setattr(script, "SCORES", tmp_path / "scores.jsonl")
    (tmp_path / "scores.jsonl").write_text(
        json.dumps({"date": "2026-08-20", "provider": "fake", "days": 1}) + "\n",
        encoding="utf-8")
    assert "2026-08-20:fake:1" in script.already()


# --- 정직성 -------------------------------------------------------------

def test_indices_never_reach_the_buy_list():
    """지수는 살 수 있는 물건이 아니다. 학습 동료로는 남기고 후보에서만 뺀다."""
    from marketlens.screen import universe

    assert "^GSPC" in universe.symbols("yahoo")
    assert "^GSPC" not in universe.buyable("yahoo")
    assert len(universe.buyable("yahoo")) >= universe.MIN_BREADTH


def test_the_table_is_not_split():
    """`PEERS is universe.UNIVERSE` 를 깨지 않고 갈래만 하나 더 만든 것이다."""
    from marketlens.api.routes import PEERS
    from marketlens.screen import universe

    assert PEERS is universe.UNIVERSE
    for name in universe.NOT_BUYABLE:
        assert any(name in row for row in universe.UNIVERSE.values()), \
            f"{name}: 어느 유니버스에도 없다 — 오타면 조용히 무효가 된다"


def test_every_market_can_still_be_ranked_after_the_exclusion():
    from marketlens.screen import universe

    for provider in universe.providers():
        assert len(universe.buyable(provider)) >= universe.MIN_BREADTH, provider


# --- 읽는 쪽 -----------------------------------------------------------

def _write(folder: Path, date: str, providers: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{date}.json").write_text(
        json.dumps({"date": date, "providers": providers}, ensure_ascii=False),
        encoding="utf-8")


def _body(symbol="AAA", degenerate=False):
    return {
        "provider": "p", "basedOn": "2026-08-28", "staleBars": 0,
        "candidates": [{"symbol": symbol, "last": 100.0,
                        "byDay": {"1": {"expected": 1.0, "band": [-2.0, 4.0],
                                        "probUp": 0.55, "confidence": "high",
                                        "source": "blend"}}}],
        "byDay": {"1": {"buy": [symbol], "avoid": [symbol],
                        "degenerate": degenerate, "allNegative": False,
                        "learned": True, "skill": 0.005}},
    }


def test_reading_merges_both_folders(tmp_path, monkeypatch):
    """Actions 는 토스를 못 돈다. 하나만 읽으면 국내주식이 통째로 사라진다."""
    from marketlens.api import recommend as layer

    local, repo = tmp_path / "local" / "recommend", tmp_path / "repo" / "recommend"
    _write(local, "2026-08-29", {"toss_kr": _body()})
    _write(repo, "2026-08-29", {"binance": _body()})
    monkeypatch.setattr(layer, "_dirs", lambda: [local, repo])
    assert set(layer._merged()) == {"toss_kr", "binance"}


def test_a_stale_market_keeps_its_own_date(tmp_path, monkeypatch):
    """PC 가 일주일 꺼져 있었으면 그 날짜가 보여야 한다 — 오늘 것인 척하면 안 된다."""
    from marketlens.api import recommend as layer

    local, repo = tmp_path / "local" / "recommend", tmp_path / "repo" / "recommend"
    _write(local, "2026-08-22", {"toss_kr": _body()})
    _write(repo, "2026-08-29", {"binance": _body()})
    monkeypatch.setattr(layer, "_dirs", lambda: [local, repo])
    merged = layer._merged()
    assert merged["toss_kr"]["date"] == "2026-08-22"
    assert merged["binance"]["date"] == "2026-08-29"


def test_a_degenerate_day_says_so(tmp_path, monkeypatch):
    """모델을 하나도 안 썼으면 그 사실이 화면까지 가야 한다."""
    from marketlens.api import recommend as layer

    folder = tmp_path / "repo" / "recommend"
    _write(folder, "2026-08-29", {"p": _body(degenerate=True)})
    monkeypatch.setattr(layer, "_dirs", lambda: [folder])
    monkeypatch.setattr(layer, "DIRS", (tmp_path / "repo",))
    found = layer.today("p", 1)
    assert found["available"] is True
    assert found["degenerate"] is True


def test_a_market_without_a_recommendation_names_the_ones_that_have_one(tmp_path, monkeypatch):
    from marketlens.api import recommend as layer

    folder = tmp_path / "repo" / "recommend"
    _write(folder, "2026-08-29", {"binance": _body()})
    monkeypatch.setattr(layer, "_dirs", lambda: [folder])
    monkeypatch.setattr(layer, "DIRS", (tmp_path / "repo",))
    found = layer.today("toss_kr", 1)
    assert found["available"] is False
    assert found["providers"] == ["binance"]


def test_the_record_needs_a_month_before_it_is_a_record(tmp_path, monkeypatch):
    """적은 표본의 좋은 숫자를 성적이라고 부르면 안 된다 — 이 저장소가 이미 배운 것이다."""
    from marketlens.api import recommend as layer

    folder = tmp_path / "repo" / "recommend"
    folder.mkdir(parents=True)
    rows = [{"date": f"2026-08-{d:02d}", "provider": "p", "days": 1,
             "buyPct": 1.0, "universePct": 0.5, "edgePct": 0.5, "bandHit": 0.8}
            for d in range(1, 6)]
    (folder / "scores.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(layer, "_dirs", lambda: [folder])
    found = layer.record("p", 1)
    assert found["n"] == 5 and found["enough"] is False
    assert found["edgePct"] == pytest.approx(0.5)


def test_the_measured_numbers_come_from_the_study_not_the_screen(tmp_path, monkeypatch):
    """화면에 숫자를 박지 않는다. 학습이 쌓은 것을 읽어 넘겨야 둘이 안 어긋난다."""
    from marketlens.api import recommend as layer

    study = tmp_path / "repo" / "study"
    study.mkdir(parents=True)
    (study / "state.json").write_text(json.dumps(
        {"overall": {"directionHit": 0.55, "bandHit": 0.82, "n": 27664,
                     "directionN": 20305}}), encoding="utf-8")
    monkeypatch.setattr(layer, "DIRS", (tmp_path / "repo",))
    assert layer.measured()["directionHit"] == 0.55
