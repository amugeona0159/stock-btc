"""추천 백필 — 되돌려 돌린 성적.

여기서 지키는 것 넷:

1. **origin 이후가 새면 안 된다** — 세 갈래(시세·사건·관심도)를 같이 자른다.
   새면 성적이 예뻐지고, 그 예쁜 숫자를 믿고 돈을 잃는다.
2. **아침 모델을 건드리면 안 된다** — 백필은 `backfill-*` 로 굽는다. `recommend-*` 를
   과거 origin 에서 잘라 구운 것으로 덮으면 며칠 전까지만 본 모델로 추천하게 된다.
3. **실전 채점과 파일이 다르다** — 섞으면 "과거에 맞았나" 가 못 믿을 숫자가 된다.
4. **줄마다 설정이 박힌다** — 설정이 바뀐 줄을 골라 다시 잴 수 있어야 한다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
STEP = 86_400_000


def _load():
    spec = importlib.util.spec_from_file_location("backfill_script",
                                                  ROOT / "scripts" / "backfill.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["backfill_script"] = module
    spec.loader.exec_module(module)
    return module


script = _load()


# --- 재료 ---------------------------------------------------------------

def _frame(n: int, seed: int = 0, start_ts: int = 1_600_000_000_000) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return pd.DataFrame({
        "ts": [start_ts + i * STEP for i in range(n)],
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": np.full(n, 1000.0), "closed": [True] * n,
    })


def _data(symbol: str, n: int = 40, seed: int = 0):
    from marketlens.events.schema import Event
    from marketlens.forecast.ml.model import SymbolData

    df = _frame(n, seed)
    events = [Event(ts=int(t), kind="news", title=f"{symbol} {i}", source="test",
                    scope=f"symbol:{symbol}", severity=0.5)
              for i, t in enumerate(df["ts"]) if i % 7 == 0]
    attention = pd.DataFrame({"attention_z": np.arange(float(n))})
    return SymbolData(symbol, df, events, attention)


# --- 미래 차단 ----------------------------------------------------------

def test_cut_removes_every_branch_after_the_origin():
    """**세 갈래를 같이 자른다.** 하나라도 남으면 나머지가 다 무의미하다 —
    사건 하나만 새도 '앞으로 무슨 일이 날지' 를 알고 예측한 셈이 된다."""
    data = _data("AAA", n=40)
    origin = int(data.df["ts"].iloc[25])
    view = script.cut(data, origin)

    assert int(view.df["ts"].max()) == origin
    assert len(view.df) == 26
    assert view.events and max(e.ts for e in view.events) <= origin
    # 관심도는 ts 열이 없다. **확정봉 수**로 잘려야 한다.
    assert len(view.attention) == 26
    assert view.attention["attention_z"].tolist() == list(range(26))


def test_cut_keeps_the_attention_row_for_row_with_the_bars():
    """행이 어긋나면 조용히 다른 날의 관심도를 보게 된다 — 값이 있으니 아무도 못 챈다."""
    data = _data("AAA", n=30)
    for i in (10, 17, 29):
        view = script.cut(data, int(data.df["ts"].iloc[i]))
        assert len(view.attention) == len(view.df) == i + 1


def test_shaking_the_future_does_not_change_the_past():
    """**origin 뒤 데이터를 3배로 흔들어도 그 시점의 세계가 안 변한다.**
    `tests/test_asof.py` 가 예측에 대해 지키는 것을 여기서는 재료에 대해 지킨다."""
    data = _data("AAA", n=40, seed=1)
    origin = int(data.df["ts"].iloc[20])
    before = script.cut(data, origin)

    shaken = data.df.copy()
    tail = shaken["ts"] > origin
    for column in ("open", "high", "low", "close", "volume"):
        shaken.loc[tail, column] = shaken.loc[tail, column] * 3
    from marketlens.events.schema import Event
    from marketlens.forecast.ml.model import SymbolData
    noisy = SymbolData(
        "AAA", shaken,
        list(data.events) + [Event(ts=origin + 5 * STEP, kind="news", title="나중 일",
                                   source="test", scope="symbol:AAA", severity=1.0)],
        data.attention.copy(),
    )
    noisy.attention.loc[noisy.attention.index > 20, "attention_z"] = -999.0

    after = script.cut(noisy, origin)
    pd.testing.assert_frame_equal(before.df, after.df)
    assert [e.title for e in before.events] == [e.title for e in after.events]
    pd.testing.assert_frame_equal(before.attention, after.attention)


def test_the_origin_grid_leaves_room_to_be_scored():
    """뒤쪽 지평 봉은 결과가 아직 안 나온 자리다. 넣으면 채점이 통째로 None 이 된다."""
    script.MIN_HISTORY, keep = 0, script.MIN_HISTORY
    try:
        loaded = [_data("AAA", n=40)]
        grid = script.origins(loaded, count=10, horizon=3)
        stamps = loaded[0].df["ts"].tolist()
        assert len(grid) == 10
        assert grid[-1] == stamps[-4]              # 마지막 3봉은 빠진다
        assert max(grid) < stamps[-1]
    finally:
        script.MIN_HISTORY = keep


def test_the_grid_is_empty_when_history_is_too_short():
    """봉이 모자라면 빈 격자다 — 억지로 앞당기면 안 데워진 지표로 예측하게 된다."""
    assert script.origins([_data("AAA", n=40)], count=10, horizon=3) == []


# --- 아침 모델을 안 건드린다 --------------------------------------------

@pytest.mark.anyio
async def test_backfill_never_bakes_over_the_morning_model():
    """`recommend-*` 는 아침에 화면이 쓰는 모델이다. 과거 origin 에서 잘라 구운 것으로
    덮으면 며칠 전까지만 본 모델로 추천하게 된다 — `study-*` 재사용 금지와 같은 이유."""
    seen: list[dict] = []

    async def fake_pick(provider, **kwargs):
        seen.append({"provider": provider, **kwargs})
        return None

    loaded = [_data(f"S{i}", n=700, seed=i) for i in range(6)]
    script.MIN_HISTORY, keep = 600, script.MIN_HISTORY
    real_pick, real_load = script.rec.pick, script.rec.load_market
    try:
        script.rec.pick = fake_pick
        script.rec.load_market = lambda provider: _ready((loaded, []))
        await script.run("fake", count=9, every=4, force=True)
    finally:
        script.rec.pick, script.rec.load_market = real_pick, real_load
        script.MIN_HISTORY = keep

    assert seen, "pick 을 한 번도 안 불렀다"
    for call in seen:
        assert call["prefix"] == "backfill"
        assert call["krw"] is False, "과거 origin 에 지금 원화 시세를 붙이면 거짓말이다"


@pytest.mark.anyio
async def test_the_views_handed_to_pick_never_reach_past_the_origin():
    """**끝에서 끝까지 본다.** `cut` 만 맞아도 부르는 쪽이 원본을 넘기면 소용없다."""
    calls: list[tuple[int, int]] = []

    async def fake_pick(provider, *, loaded, **kwargs):
        # `pick` 은 `loaded[0]` 의 확정봉에서 `lastTs` 를 읽는다. 그게 origin 이어야 한다.
        calls.append((int(loaded[0].df["ts"].max()),
                      max(int(d.df["ts"].max()) for d in loaded)))
        return None

    full = [_data(f"S{i}", n=700, seed=i) for i in range(6)]
    script.MIN_HISTORY, keep = 600, script.MIN_HISTORY
    real_pick, real_load = script.rec.pick, script.rec.load_market
    try:
        script.rec.pick = fake_pick
        script.rec.load_market = lambda provider: _ready((full, []))
        await script.run("fake", count=6, every=3, force=True)
    finally:
        script.rec.pick, script.rec.load_market = real_pick, real_load
        script.MIN_HISTORY = keep

    grid = script.origins(full, count=6, horizon=3)
    assert [c[0] for c in calls] == grid          # origin 그 봉이 마지막 봉이다
    for origin, latest in calls:
        assert latest <= origin, "origin 뒤 봉이 그대로 넘어갔다"


async def _ready(value):
    return value


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- 파일을 나눈다 -------------------------------------------------------

def test_the_backfill_file_is_not_the_live_scoreboard():
    """섞으면 "과거에 맞았나" 가 못 믿을 숫자가 된다. 백필은 origin 을 내가 고를 수
    있고 몇 번이든 다시 돌릴 수 있는 반면, 실전은 그날 한 번뿐이다."""
    assert script.OUT != script.rec.SCORES
    assert script.OUT.name == "backfill.jsonl"
    assert script.rec.SCORES.name == "scores.jsonl"
    assert script.OUT.parent == script.rec.SCORES.parent


def test_every_row_carries_the_settings_it_was_measured_with(tmp_path, monkeypatch):
    """설정이 바뀌면 옛 줄과 새 줄을 한 숫자에 섞을 수 없다. 이름이 있으면 바뀐
    줄만 골라 다시 잴 수 있다."""
    monkeypatch.setattr(script, "OUT", tmp_path / "backfill.jsonl")
    setting = script.config(8)
    assert setting["window"] == script.rec.WINDOW
    assert setting["folds"] == script.rec.FOLDS
    assert setting["buy"] == script.rec.BUY

    script.write([{"date": "2026-01-01", "provider": "fake", "days": 1,
                   "config": setting, "holdout": False, "edgePct": 0.1}])
    back = script.read()
    assert len(back) == 1 and back[0]["config"] == setting
    assert script.key(back[0]) == ("2026-01-01", "fake", 1)


def test_the_holdout_is_the_last_stretch_not_a_random_slice():
    """시간순으로 잘라야 한다. 무작위로 고르면 튜닝 구간과 같은 시기가 섞여
    '안 본 구간' 이 아니게 된다."""
    assert 0 < script.HOLDOUT_SHARE < 0.5


def test_the_settings_live_in_one_place():
    """백필 기록이 박아 두는 설정은 `recommend.py` 것 그대로여야 한다.
    두 벌이 되면 '무슨 설정으로 잰 성적인지' 가 조용히 어긋난다."""
    source = (ROOT / "scripts" / "recommend.py").read_text(encoding="utf-8")
    assert "window=WINDOW" in source and "folds=FOLDS" in source, \
        "pick() 이 손잡이를 상수 대신 숫자로 들고 있다"


def test_promotion_never_reads_the_backfill():
    """승격 판정은 워크포워드로만. as-of 를 승격 기준에 넣는 순간 그것도 학습
    구간이 되어 외부 표본이 아니게 된다."""
    daily = (ROOT / "scripts" / "daily.py").read_text(encoding="utf-8")
    assert "backfill" not in daily


# --- 잡음과 가르기 -------------------------------------------------------

def test_a_noise_series_is_not_called_a_finding():
    """**제일 중요한 판.** 실력이 없는 계열에 작은 p 를 붙이면 이 검정이 오히려
    거짓을 만들어 낸다. 평균 0짜리 잡음은 p 가 0.05 언저리로 내려가면 안 된다."""
    rng = np.random.default_rng(20260830)
    noise = list(rng.normal(0, 1.0, 200))
    got = script.verdict(noise, noise, rounds=400)
    assert got["p"] > 0.2, f"잡음에 p={got['p']} — 검정이 거짓을 만든다"
    assert got["lo"] < 0 < got["hi"], "구간이 0 을 안 품는다"


def test_a_real_edge_is_found():
    """반대쪽도 지킨다. 모든 판이 +2%p 면 그건 우연이 아니다 — 여기가 안 걸리면
    검정이 아무것도 못 잡는 장식이 된다."""
    rng = np.random.default_rng(11)
    strong = list(2.0 + rng.normal(0, 0.5, 60))
    got = script.verdict(strong, strong, rounds=400)
    assert got["p"] < 0.05
    assert got["lo"] > 0
    # 흔들림이 없는 계열도 터지지 않아야 한다 — 최적 블록 계산이 0 으로 나눈다.
    flat = script.verdict([2.0] * 60, [2.0] * 60, rounds=100)
    assert flat["block"] == 1 and flat["mean"] == 2.0


def test_the_block_length_is_measured_not_chosen():
    """손으로 고른 덩어리 길이 위에 p 가 서면 그 p 는 그 임의의 숫자 것이다.
    `overfit.pick_block` 이 Politis & White 최적값으로 정한다."""
    source = (ROOT / "scripts" / "backfill.py").read_text(encoding="utf-8")
    assert "overfit.pick_block" in source
    # 상수로 박아 둔 덩어리 길이가 없어야 한다.
    assert "block = 200" not in source and "BLOCK =" not in source


def test_the_block_length_comes_from_the_long_series():
    """holdout 12판으로 자기상관을 재면 그 값이 곧 잡음이다. 전체 계열에서 재서
    짧은 구간에도 그대로 쓴다."""
    rng = np.random.default_rng(7)
    whole = list(rng.normal(0, 1, 200))
    short = whole[-12:]
    got = script.verdict(short, whole, rounds=200)
    assert got["n"] == 12
    # 12판짜리에서 잰 것보다 크거나 같아야 한다(짧은 계열은 상한 n//4=3 에 걸린다).
    assert 1 <= got["block"] <= 3


def test_too_few_hands_get_no_verdict():
    """네 판으로는 아무 말도 못 한다. 억지로 숫자를 내면 그게 거짓이다."""
    assert script.verdict([1.0, 2.0], [1.0, 2.0])["mean"] is None


def test_the_number_of_cells_looked_at_is_reported(capsys):
    """**서른 칸 중 하나가 0.05 아래인 것은 발견이 아니다.** 귀무에서도 제일 작은
    p 는 대략 1/(칸+1) 근처에 온다. 그 사실을 같이 안 내면 오독이 난다 —
    이 저장소는 이미 두 곳에서 시험 횟수를 기록에 남긴다."""
    rng = np.random.default_rng(3)
    rows = []
    for provider in ("a", "b"):
        for days in (1, 2, 3):
            for i in range(40):
                rows.append({"provider": provider, "days": days, "origin": i,
                             "edgePct": float(rng.normal(0, 1)),
                             "holdout": i >= 32})
    script.test(rows)
    out = capsys.readouterr().out
    assert "칸 12개를 봤다" in out
    assert "제일 좋은 칸" in out
