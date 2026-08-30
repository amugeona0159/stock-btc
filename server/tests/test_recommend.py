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
    """**`pick()` 이 실제로 얼리는 모양 그대로.** 추천 목록은 `byDay[일수]` 안의
    심볼 문자열 리스트다 — 지평마다 다른 종목을 고르니 최상위에 있을 수가 없다.

    여기서 지어낸 모양(`"buy": [{"symbol": …}]`)을 검사하던 시절이 있었고, 그래서
    `score_one` 이 없는 키를 읽어 `KeyError` 로 죽는 걸 테스트가 못 잡았다.
    채점은 ①단계라 뽑기까지 같이 못 돌았다. **픽스처는 생산 코드가 만드는 것만 흉내낸다.**
    """
    body = {
        "provider": "fake", "lastTs": last_ts, "basedOn": "2026-08-20",
        "candidates": [{"symbol": s, "byDay": {"1": {"expected": 0.0,
                                                     "band": [-1.0, 1.0]}}}
                       for s in symbols],
        "byDay": {"1": {"buy": list(buy), "avoid": list(avoid),
                        "model": "recommend-fake-1d-1"}},
    }
    frozen = {"date": "2026-08-20", "providers": {"fake": body}}
    (tmp_path / "2026-08-20.json").write_text(json.dumps(frozen, ensure_ascii=False),
                                              encoding="utf-8")
    return frozen, body


def test_the_frozen_file_on_disk_can_actually_be_scored(tmp_path, monkeypatch):
    """**얼린 파일을 그대로 넣어 본다.** 픽스처가 아니라 `pick()` 이 만든 진짜 모양이다 —
    여기가 어긋나 있었고, 어긋난 줄 모르는 채로 아침마다 뽑기만 돌았다."""
    import asyncio

    import pandas as pd

    anchor = 1_000_000_000_000
    symbols = [f"S{i}" for i in range(6)]
    body = {
        # `pick()` 의 반환 그대로: byDay 의 buy/avoid 는 **심볼 문자열**이다.
        "provider": "fake", "lastTs": anchor, "basedOn": "2026-08-20",
        "staleBars": 0,
        "candidates": [{"symbol": s, "last": 100.0, "lastTs": anchor,
                        "byDay": {"2": {"day": 2, "expected": 0.5,
                                        "band": [-5.0, 5.0]}}} for s in symbols],
        "byDay": {"2": {"buy": symbols[:3], "avoid": symbols[-2:],
                        "degenerate": False, "allNegative": False, "learned": True,
                        "skill": 0.004, "modelStale": False,
                        "model": "recommend-fake-1d-2"}},
        "skipped": [],
    }
    frozen = {"date": "2026-08-20", "providers": {"fake": body}}

    class Flat:
        async def history(self, symbol, timeframe, limit):
            step = 86_400_000
            return pd.DataFrame({
                "ts": [anchor + i * step for i in range(4)],
                "open": [100.0] * 4, "high": [100.0] * 4, "low": [100.0] * 4,
                "close": [100.0, 101.0, 102.0, 103.0],
                "volume": [1.0] * 4, "closed": [True] * 4,
            })

    monkeypatch.setattr(script, "get_provider", lambda key: Flat())
    row = asyncio.run(script.score_one(frozen, "fake", body, 2))
    assert row is not None, "얼린 파일을 채점하지 못한다 — 여기가 죽으면 뽑기도 같이 죽는다"
    assert row["buyPct"] == pytest.approx(2.0)                 # 100 → 102
    assert row["model"] == "recommend-fake-1d-2"               # 어느 모델이 낸 추천인지
    # 안 뽑은 지평은 채점할 게 없다 — 실패가 아니라 없음이다.
    assert asyncio.run(script.score_one(frozen, "fake", body, 1)) is None


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


# --------------------------------------------------------------- 폴더의 주인

def test_actions_folder_never_holds_korean_stocks():
    """`learning/` 은 Actions 것이고, **Actions 는 토스에 못 닿는다**(IP 허용 목록).

    거기 국내주식이 들어 있으면 PC 에서 만든 파일을 잘못 커밋한 것이다. 실제로 그랬다 —
    개발 중에 만든 8/29 파일에 toss_kr·toss_us 가 들어간 채 올라갔다.
    합쳐 읽을 때 로컬이 이기니 화면은 멀쩡해 보이는데, 저장소만 보면 Actions 가
    국내주식을 뽑은 것처럼 읽힌다. PC 를 안 켠 사람에게는 그게 사실이 아니다.
    """
    import json
    from pathlib import Path

    folder = Path(__file__).resolve().parents[2] / "learning" / "recommend"
    for path in sorted(folder.glob("20*.json")):
        body = json.loads(path.read_text(encoding="utf-8"))
        found = sorted(p for p in (body.get("providers") or {}) if p.startswith("toss"))
        assert not found, f"{path.name}: Actions 폴더에 {found} — learning-local 로 가야 한다"


def test_krw_lookup_is_asked_only_of_crypto_markets():
    """**코인인지 아닌지는 심볼 모양이 아니라 그 시장이 안다.**

    모양으로 판단하면 `AAPL` 이 코인처럼 보인다 — 거래쌍 접미사가 없으니 그대로
    남고, `KRW-AAPL` 이라는 없는 마켓을 업비트에 물으러 간다. 실제로 그랬고,
    미국주식 추천 한 번마다 실패하는 호출이 아홉 번씩 나갔다.
    """
    from marketlens.providers import get as get_provider
    from marketlens.screen import coins, universe

    crypto = {p for p in universe.providers()
              if get_provider(p).info.market == "crypto"}
    assert crypto == {"binance", "upbit"}, crypto

    # **모양만 보면 주식이 코인처럼 보인다.** 이 값이 그럴듯하게 나온다는 것이
    # 시장으로 갈라야 하는 이유다 — 업비트에 `KRW-AAPL` 은 없다.
    assert coins.krw_market("AAPL") == "KRW-AAPL"

    # 주식 시장의 후보에는 코인 규칙을 대지 않는다. 대면 전부 없는 마켓이 된다.
    stock_symbols = [s for p in universe.providers() if p not in crypto
                     for s in universe.buyable(p)]
    assert stock_symbols, "주식 후보가 하나도 없다 — 이 검사가 뜻을 잃었다"
    assert all(coins.base(s) == s for s in stock_symbols), (
        "주식 심볼이 거래쌍처럼 잘렸다")


# --- 되돌려 본 성적 -----------------------------------------------------

def _rows(folder: Path, name: str, rows: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(
        "".join(json.dumps(r, ensure_ascii=False) + chr(10) for r in rows),
        encoding="utf-8")


def _line(date: str, days: int, edge: float, holdout: bool) -> dict:
    return {"date": date, "provider": "p", "days": days, "buyPct": edge,
            "universePct": 0.0, "edgePct": edge, "bandHit": 0.8,
            "holdout": holdout, "mode": "backfill", "model": "backfill-p-1d-1"}


def test_the_backfill_never_leaks_into_the_live_record(tmp_path, monkeypatch):
    """**섞으면 "과거에 맞았나" 가 못 믿을 숫자가 된다.** 실전은 그날 한 번뿐이고
    백필은 몇 번이든 다시 돌릴 수 있어, 한 숫자로 합치면 몇 번 돌렸는지가 성적에
    스며든다. 여기서는 백필만 잔뜩 넣고 실전 칸이 0 인지 본다."""
    from marketlens.api import recommend as layer

    folder = tmp_path / "repo" / "recommend"
    _rows(folder, layer.BACKFILL, [_line(f"2026-06-{i:02d}", 1, 1.0, False)
                                   for i in range(1, 20)])
    monkeypatch.setattr(layer, "_dirs", lambda: [folder])

    assert layer.record("p", 1) == {"n": 0, "enough": False}
    back = layer.backfill("p", 1)
    assert back["n"] == 19 and back["edgePct"] == 1.0


def test_the_backfill_reads_the_stretch_it_did_not_tune_on(tmp_path, monkeypatch):
    """규칙을 고른 구간에서 잰 성적은 자기 답을 보고 만든 값이다. `holdout` 은
    거기 안 쓴 마지막 구간이라, **읽을 값은 그쪽**이다."""
    from marketlens.api import recommend as layer

    folder = tmp_path / "repo" / "recommend"
    _rows(folder, layer.BACKFILL,
          [_line(f"2026-06-{i:02d}", 1, 5.0, False) for i in range(1, 9)]
          + [_line(f"2026-07-{i:02d}", 1, -1.0, True) for i in range(1, 3)])
    monkeypatch.setattr(layer, "_dirs", lambda: [folder])

    back = layer.backfill("p", 1)
    assert back["n"] == 10 and back["edgePct"] == pytest.approx(3.8)
    # 튜닝 구간의 +5.0 이 섞이면 안 된다.
    assert back["holdout"]["n"] == 2 and back["holdout"]["edgePct"] == -1.0
    assert back["from"] == "2026-06-01" and back["to"] == "2026-07-02"


def test_the_backfill_is_carried_next_to_the_record_not_inside_it(tmp_path, monkeypatch):
    """화면이 어느 쪽 숫자인지 밝힐 수 있어야 한다."""
    from marketlens.api import recommend as layer

    folder = tmp_path / "repo" / "recommend"
    _write(folder, "2026-08-29", {"p": _body()})
    _rows(folder, layer.BACKFILL, [_line("2026-06-01", 1, 2.0, True)])
    monkeypatch.setattr(layer, "_dirs", lambda: [folder])
    monkeypatch.setattr(layer, "DIRS", (tmp_path / "repo",))

    found = layer.today("p", 1)
    assert found["record"]["n"] == 0
    assert found["backfill"]["n"] == 1
    assert found["backfill"]["model"] == "backfill-p-1d-1"


def test_rows_measured_with_another_setting_are_not_mixed_in(tmp_path, monkeypatch):
    """**손잡이가 바뀌면 옛 모델 성적과 새 모델 성적이 한 숫자가 된다** — 그게
    정확히 못 믿을 숫자다. 최근에 잰 설정만 세고, 안 센 줄은 세어서 알린다."""
    from marketlens.api import recommend as layer

    old_cfg, new_cfg = {"window": 32}, {"window": 48}
    rows = []
    for i in range(1, 6):
        rows.append({**_line(f"2026-05-{i:02d}", 1, 9.0, True),
                     "config": old_cfg, "scoredAt": "2026-06-01T00:00:00+00:00"})
    for i in range(1, 4):
        rows.append({**_line(f"2026-07-{i:02d}", 1, 1.0, True),
                     "config": new_cfg, "scoredAt": "2026-08-01T00:00:00+00:00"})

    folder = tmp_path / "repo" / "recommend"
    _rows(folder, layer.BACKFILL, rows)
    monkeypatch.setattr(layer, "_dirs", lambda: [folder])

    back = layer.backfill("p", 1)
    assert back["n"] == 3, "옛 설정 줄이 섞였다"
    assert back["edgePct"] == 1.0                  # 9.0 이 섞이면 4.0 이 된다
    assert back["staleRows"] == 5                  # 조용히 버리지 않는다


def test_a_market_without_a_backfill_says_nothing(tmp_path, monkeypatch):
    """빈 목록보다 그럴듯한 숫자가 나쁘다."""
    from marketlens.api import recommend as layer

    monkeypatch.setattr(layer, "_dirs", lambda: [tmp_path / "none"])
    assert layer.backfill("p", 1) == {"n": 0}


# --- 세 묶음 -----------------------------------------------------------

def test_the_three_groups_come_at_once(tmp_path, monkeypatch):
    """**화면이 시장을 고르게 하지 않는다.** 차트는 기본이 BTCUSDT 라, 선 시장 하나만
    보여주면 열 때마다 코인 추천만 나온다 — 국내주식·해외주식이 있다는 걸 먼저
    알아야 볼 수 있었다."""
    from marketlens.api import recommend as layer

    folder = tmp_path / "repo" / "recommend"
    _write(folder, "2026-08-29", {"toss_kr": _body("005930"),
                                  "yahoo": _body("AAPL"),
                                  "binance": _body("SOLUSDT")})
    monkeypatch.setattr(layer, "_dirs", lambda: [folder])
    monkeypatch.setattr(layer, "DIRS", (tmp_path / "repo",))

    found = layer.groups(1)
    assert [g["key"] for g in found["groups"]] == ["kr", "us", "coin"]
    assert [g["label"] for g in found["groups"]] == ["국내주식", "해외주식", "코인"]
    assert all(g["available"] for g in found["groups"])
    assert [g["provider"] for g in found["groups"]] == ["toss_kr", "yahoo", "binance"]


def test_a_missing_group_keeps_its_place(tmp_path, monkeypatch):
    """국내주식은 토스가 IP 허용목록을 타서 Actions 에서 못 돈다. PC 가 며칠 꺼져
    있으면 통째로 없는데, **없는 것을 안 보여주면 "국내주식은 살 게 없다"로 읽힌다.**"""
    from marketlens.api import recommend as layer

    folder = tmp_path / "repo" / "recommend"
    _write(folder, "2026-08-29", {"binance": _body("SOLUSDT")})
    monkeypatch.setattr(layer, "_dirs", lambda: [folder])
    monkeypatch.setattr(layer, "DIRS", (tmp_path / "repo",))

    by_key = {g["key"]: g for g in layer.groups(1)["groups"]}
    assert len(by_key) == 3                       # 자리는 셋 그대로
    assert by_key["kr"]["available"] is False
    assert "국내주식" in by_key["kr"]["reason"]
    assert by_key["coin"]["available"] is True


def test_a_group_prefers_the_first_provider_listed(tmp_path, monkeypatch):
    """미국주식은 토스와 야후 둘 다 준다. 토스가 앞인 건 한글 이름이 붙어서다 —
    둘이 다 있을 때 어느 쪽을 쓰는지가 정해져 있어야 화면이 날마다 안 흔들린다."""
    from marketlens.api import recommend as layer

    folder = tmp_path / "repo" / "recommend"
    _write(folder, "2026-08-29", {"yahoo": _body("AAPL"), "toss_us": _body("MSFT")})
    monkeypatch.setattr(layer, "_dirs", lambda: [folder])
    monkeypatch.setattr(layer, "DIRS", (tmp_path / "repo",))

    us = next(g for g in layer.groups(1)["groups"] if g["key"] == "us")
    assert us["provider"] == "toss_us"
    order = dict((key, names) for key, _, names in layer.GROUPS)
    assert order["us"].index("toss_us") < order["us"].index("yahoo")


def test_every_group_provider_is_a_real_universe():
    """묶음 표에 오타가 있으면 그 묶음이 조용히 영영 비어 보인다."""
    from marketlens.api.recommend import GROUPS
    from marketlens.screen import universe

    known = set(universe.providers())
    for key, name, order in GROUPS:
        assert order, f"{key}: 프로바이더가 비었다"
        for provider in order:
            assert provider in known, f"{name}: {provider} 는 유니버스에 없다"


def test_buy_and_avoid_are_the_same_size():
    """화면이 세 묶음을 나란히 놓으므로 양쪽이 같은 크기여야 한 줄로 읽힌다."""
    assert script.BUY == script.AVOID == 3
    # 후보가 그 둘을 합친 것보다 많아야 순위가 뜻을 갖는다.
    from marketlens.screen import universe

    for provider in universe.providers():
        assert len(universe.buyable(provider)) > script.BUY + script.AVOID, provider


# --- 언제 사는 게 좋은가 (지평별로 어떻게 봤나) -------------------------

def test_a_symbol_can_be_read_horizon_by_horizon(tmp_path, monkeypatch):
    """**"추천은 사라는데 판단은 팔라네" 의 답이 여기 있다.**

    아침 추천은 1·2·3일을 각각 봤고 그 답이 서로 다를 수 있다 — 실제로
    삼성바이오로직스가 하루 뒤에는 `사라`, 이틀 뒤에는 `사지 말 것` 이었다.
    화면이 그걸 못 보여주면 두 화면이 싸우는 것처럼만 읽힌다.
    """
    from marketlens.api import recommend as layer

    body = {
        "provider": "p", "lastTs": 1, "basedOn": "2026-08-29",
        "candidates": [{"symbol": "AAA", "last": 100.0, "byDay": {
            "1": {"expected": 1.0, "band": [-2.0, 4.0], "probUp": 0.6,
                  "confidence": "high"},
            "2": {"expected": -0.5, "band": [-3.0, 2.0], "probUp": 0.45,
                  "confidence": "low"},
        }}],
        "byDay": {"1": {"buy": ["AAA"], "avoid": ["ZZZ"]},
                  "2": {"buy": ["ZZZ"], "avoid": ["AAA"]}},
    }
    folder = tmp_path / "repo" / "recommend"
    _write(folder, "2026-08-29", {"p": body})
    monkeypatch.setattr(layer, "_dirs", lambda: [folder])

    found = layer.for_symbol("p", "AAA")
    assert found["available"] is True
    assert found["days"]["1"]["side"] == "buy"
    assert found["days"]["2"]["side"] == "avoid"     # 같은 종목, 다른 지평, 다른 답
    assert found["days"]["1"]["expected"] == 1.0


def test_a_symbol_that_was_never_a_candidate_says_so(tmp_path, monkeypatch):
    """후보가 아니었으면 지어내지 않는다. 빈 화면이 그럴듯한 문장보다 낫다."""
    from marketlens.api import recommend as layer

    folder = tmp_path / "repo" / "recommend"
    _write(folder, "2026-08-29", {"p": _body("AAA")})
    monkeypatch.setattr(layer, "_dirs", lambda: [folder])

    assert layer.for_symbol("p", "ZZZ") == {"available": False}
    assert layer.for_symbol("없는시장", "AAA") == {"available": False}


def test_the_side_says_where_it_sat_not_how_much(tmp_path, monkeypatch):
    """**"몇 위" 가 아니라 "어느 셋" 이다.** 순위는 그 묶음 안에서만 뜻이 있고,
    기대값 차이가 0.03%p 여도 1위는 1위라 숫자로 주면 과하게 읽힌다."""
    from marketlens.api import recommend as layer

    folder = tmp_path / "repo" / "recommend"
    _write(folder, "2026-08-29", {"p": _body("AAA")})
    monkeypatch.setattr(layer, "_dirs", lambda: [folder])

    found = layer.for_symbol("p", "AAA")
    assert set(found["days"]["1"]) >= {"expected", "band", "probUp", "confidence", "side"}
    assert found["days"]["1"]["side"] in {"buy", "avoid", "mid"}
