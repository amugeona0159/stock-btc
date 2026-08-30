"""추천 백필 — 과거 아침에 서서 뽑고, 지평이 지난 뒤 실제와 맞춘다.

화면의 "이 추천이 과거에 맞았나" 는 실전 채점(`learning/recommend/scores.jsonl`)이
쌓여야 답이 나오는데, 그건 하루에 한 줄씩이라 한 달을 기다려야 한 달치다.
여기서는 **같은 추천을 과거로 되돌려 돌려** 그 표를 미리 채운다.

## 되돌려 돌리는 게 왜 되나

`scripts/recommend.py: pick()` 은 **넘겨받은 시세로 그 자리에서 굽는다.** 그래서
시세를 origin 까지 잘라 넘기면 그대로 as-of 가 된다 — 백필용 뽑기 함수를 따로
만들지 않는 이유다. 채점도 `score_one` 그대로 쓰고 시세가 어디서 오는지만 바꾼다.
**표가 두 벌이 되면 "되돌려 본 성적" 과 "실전 성적" 이 다른 자로 잰 값이 된다.**

## 미래를 막는 곳 — 자르는 데가 넷이다

`cut()` 이 origin 이후를 시세·사건·관심도에서 통째로 잘라 내고, 학습 표는 잘린
시세에서 다시 만들어지므로 같이 잘린다. `scripts/asof.py` 와 같은 네 갈래다.
`tests/test_backfill.py` 가 "origin 뒤 데이터를 흔들어도 그 시점 추천이 안 변한다"로
지킨다. **하나라도 새면 성적이 예뻐지고, 그 예쁜 숫자를 믿고 돈을 잃는다.**

## 8 origin 마다 굽는다

(시장 × 지평 3 × origin)마다 구우면 60일치가 180번이라 몇 시간이다. 매번은 너무
비싸고 한 번만 하면 미래를 본다 — `scripts/asof.py` 가 같은 벽에서 쓴 답이다.

## 실전 채점과 파일을 나눈다

`scores.jsonl`(실전)과 `backfill.jsonl`(되돌려 본 것)은 다른 파일이다. 섞으면
"과거에 맞았나" 가 못 믿을 숫자가 된다 — 백필은 origin 을 내가 고를 수 있고 몇 번이든
다시 돌릴 수 있는 반면 실전은 그날 한 번뿐이라, 성질이 다른 숫자다. 화면도 어느
쪽인지 밝힌다.

**줄마다 모델 이름과 설정을 박는다.** 설정이 바뀌면 `--restale` 이 옛 설정 줄을
버리고 그 자리만 다시 돌린다. 옛 모델 성적과 새 모델 성적이 한 숫자에 섞이면 그게
정확히 못 믿을 숫자다.

## 승격에 쓰지 않는다

CLAUDE.md 의 규칙 그대로 — 모델 승격 판정은 워크포워드(`scripts/daily.py`)로만 한다.
백필은 as-of 라, 승격 기준에 넣는 순간 그것도 학습 구간이 되어 외부 표본이 아니게
된다. 백필로는 **추천 규칙**(후보 수·기권 문턱·지평)만 만지고, 마지막 구간(`holdout`)은
튜닝에 안 쓰고 성적 보고용으로 남긴다.

돌리는 법:
    .venv/Scripts/python scripts/backfill.py --provider binance --origins 12
    .venv/Scripts/python scripts/backfill.py --provider binance --origins 60
    .venv/Scripts/python scripts/backfill.py --summary
    .venv/Scripts/python scripts/backfill.py --test
    .venv/Scripts/python scripts/backfill.py --provider binance --restale
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _recommend():
    """`scripts/recommend.py` 를 그대로 불러 쓴다. 뽑기·채점을 여기서 다시 적으면
    표가 두 벌이 되고, 그 순간 두 성적을 나란히 놓을 수가 없다."""
    spec = importlib.util.spec_from_file_location("recommend_script",
                                                  ROOT / "scripts" / "recommend.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["recommend_script"] = module
    spec.loader.exec_module(module)
    return module


rec = _recommend()

from marketlens.core.candle import closed_only          # noqa: E402
from marketlens.forecast.ml.model import SymbolData     # noqa: E402

OUT = rec.OUT / "backfill.jsonl"
# 매 origin 마다 구우면 너무 비싸고, 한 번만 구우면 미래를 본다(`scripts/asof.py`).
RETRAIN_EVERY = 8
# 뒤 20% 는 규칙 튜닝에 쓰지 않는다. 튜닝에 쓴 구간에서 잰 성적은 자기 답을 보고
# 만든 성적이라 못 믿는다.
HOLDOUT_SHARE = 0.2
# 학습 표가 데워질 자리. 이보다 앞 origin 은 잡지 않는다.
MIN_HISTORY = 600


# --- 자르기 -------------------------------------------------------------

def cut(data: SymbolData, origin_ts: int) -> SymbolData:
    """origin 이후를 전부 잘라 낸 '그때까지의 세계'.

    **세 갈래를 같이 자른다** — 시세·사건·관심도. 학습 표는 잘린 시세에서 다시
    만들어지므로 같이 잘린다(네 번째 갈래). 하나라도 남으면 그 뒤 전부가 무의미하다.

    관심도 축은 `_symbol_data` 가 `closed_only(df)` 에 맞춰 만든 것이라 **확정봉과
    행이 일대일**이다. 그래서 ts 가 아니라 **남은 확정봉 수**로 자른다 — ts 로 자르려
    들면 관심도에 ts 열이 없어 조용히 안 잘린다.

    `scripts/asof.py` 에도 같은 일을 하는 `cut` 이 있다. 그쪽은 확정봉만 든 `Slice`
    를 받아서 하나로 합치지 않았다 — **둘 다 맞아야 하므로 한쪽을 고치면 다른 쪽도 본다.**
    """
    keep = data.df["ts"].to_numpy() <= origin_ts
    df = data.df[keep].reset_index(drop=True)
    attention = data.attention
    if attention is not None and len(attention):
        closed_ts = closed_only(data.df)["ts"].to_numpy()
        rows = int((closed_ts <= origin_ts).sum())
        attention = (attention.iloc[:rows].reset_index(drop=True)
                     if len(attention) == len(closed_ts) else None)
    return SymbolData(data.symbol, df,
                      [e for e in (data.events or []) if e.ts <= origin_ts],
                      attention)


def origins(loaded: list, count: int, horizon: int) -> list[int]:
    """되돌아갈 아침들. `pick()` 이 `loaded[0]` 의 확정봉에서 `lastTs` 를 읽으므로
    격자도 거기서 뽑는다 — 그래야 origin 과 예측이 딛고 선 봉이 같은 봉이다.

    뒤쪽 `horizon` 봉은 뺀다. 결과가 아직 안 나온 자리라 채점이 안 된다.
    """
    closed = closed_only(loaded[0].df).reset_index(drop=True)
    stamps = closed["ts"].to_numpy()
    last = len(stamps) - horizon - 1
    first = max(MIN_HISTORY, last - count + 1)
    if last < first:
        return []
    return [int(stamps[i]) for i in range(first, last + 1)]


# --- 기록 ---------------------------------------------------------------

def config(every: int) -> dict:
    """이 성적이 **무슨 설정으로 잰 것인지.** 줄마다 박아 둔다 — 설정이 바뀌면
    옛 줄과 새 줄을 한 숫자에 섞을 수 없기 때문이다."""
    return {"window": rec.WINDOW, "folds": rec.FOLDS, "bars": rec.BARS,
            "buy": rec.BUY, "avoid": rec.AVOID, "retrainEvery": every}


def read() -> list[dict]:
    if not OUT.is_file():
        return []
    rows = []
    for line in OUT.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def write(rows: list[dict]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                   encoding="utf-8")


def key(row: dict) -> tuple:
    return (row.get("date"), row.get("provider"), row.get("days"))


# --- 한 시장 -------------------------------------------------------------

async def run(provider: str, count: int, every: int, force: bool,
              save=None) -> list[dict]:
    """`save(rows)` 를 origin 마다 부른다. **끝에 한 번 저장하면 안 된다** — 한 시장이
    30분짜리라, 도중에 끊기면 그때까지 구운 것이 통째로 사라진다."""
    started = time.time()
    print(f"\n{provider}: 시세 적재 중 …")
    loaded, skipped = await rec.load_market(provider)
    if len(loaded) < rec.universe.MIN_BREADTH:
        print(f"  시세를 받은 종목이 {len(loaded)}개뿐 — 되돌릴 게 없다")
        return []

    # 채점용 **전체** 시세. origin 마다 다시 물으면 (origin × 종목)번이 된다.
    truth = {d.symbol: closed_only(d.df).reset_index(drop=True) for d in loaded}
    grid = origins(loaded, count, max(rec.DAYS))
    if not grid:
        print("  봉이 모자라 origin 을 못 잡는다")
        return []

    setting = config(every)
    done = {key(r) for r in read()
            if r.get("provider") == provider and not force
            and r.get("config") == setting}
    # 뒤 20% 는 규칙 튜닝에 안 쓴다.
    holdout_from = grid[int(len(grid) * (1 - HOLDOUT_SHARE))]

    print(f"  origin {len(grid)}개 · {pd.Timestamp(grid[0], unit='ms'):%Y-%m-%d}"
          f" ~ {pd.Timestamp(grid[-1], unit='ms'):%Y-%m-%d}"
          f" · 학습 {len(range(0, len(grid), every))}회 × 지평 {len(rec.DAYS)}")

    fresh: list[dict] = []
    trained_at: int | None = None
    for i, origin_ts in enumerate(grid):
        day = f"{pd.Timestamp(origin_ts, unit='ms'):%Y-%m-%d}"
        retrain = i % every == 0
        if retrain:
            trained_at = origin_ts
        if not retrain and all((day, provider, d) in done for d in rec.DAYS):
            continue

        view = [cut(d, origin_ts) for d in loaded]
        body = await rec.pick(provider, loaded=view, skipped=skipped,
                              prefix="backfill", retrain=retrain, krw=False, quiet=True)
        if body is None:
            continue
        frozen = {"date": day, "providers": {provider: body}}
        made: list[dict] = []
        for days in rec.DAYS:
            if (day, provider, days) in done:
                continue
            row = await rec.score_one(frozen, provider, body, days, closes=truth)
            if row is None:
                continue
            row.update({
                "mode": "backfill",
                "origin": origin_ts,
                # 그 예측을 낸 모델이 **언제 서서 구워진 것인지.** 8 origin 마다
                # 다시 굽기 때문에 origin 과 다를 수 있고, 그 간격만큼 모델이
                # 낡은 채로 예측한 것이다 — 실전보다 불리하면 불리했지 유리하지 않다.
                "trainedAt": trained_at,
                "config": setting,
                "holdout": origin_ts >= holdout_from,
            })
            fresh.append(row)
            made.append(row)

        if made and save is not None:
            save(provider, made)
        if (i + 1) % every == 0 or i == len(grid) - 1:
            print(f"  {i + 1:3d}/{len(grid)}  {day}  누적 {len(fresh)}줄"
                  f"  · {time.time() - started:.0f}s", flush=True)

    return fresh


# --- 요약 ---------------------------------------------------------------

def summary(rows: list[dict]) -> None:
    """**튜닝 구간과 holdout 을 따로 낸다.** 합치면 규칙을 고른 구간의 성적이
    섞여 들어가 실제보다 좋게 나온다."""
    if not rows:
        print("아직 백필한 게 없다")
        return
    print(f"\n{'시장':10s} {'지평':>5s} {'구간':>8s} {'판':>4s} "
          f"{'추천':>8s} {'후보평균':>9s} {'차이':>9s} {'이긴비율':>8s} {'밴드':>7s}")
    for provider in sorted({r["provider"] for r in rows}):
        for days in rec.DAYS:
            for label, want in (("튜닝", False), ("holdout", True)):
                part = [r for r in rows if r["provider"] == provider
                        and r["days"] == days and bool(r.get("holdout")) == want]
                if not part:
                    continue
                print(f"{provider:10s} {days:4d}일 {label:>8s} {len(part):4d} "
                      f"{np.mean([r['buyPct'] for r in part]):+7.2f}% "
                      f"{np.mean([r['universePct'] for r in part]):+8.2f}% "
                      f"{np.mean([r['edgePct'] for r in part]):+8.2f}%p "
                      f"{np.mean([r['edgePct'] > 0 for r in part]) * 100:7.0f}% "
                      f"{np.mean([r.get('bandHit') or 0 for r in part]) * 100:6.1f}%")
    settings = {json.dumps(r.get("config"), sort_keys=True) for r in rows}
    if len(settings) > 1:
        print(f"\n  ⚠ 설정이 {len(settings)}가지 섞여 있다. 화면은 가장 최근 설정만"
              "\n    세므로 판 수가 여기보다 적게 보인다 — `--restale` 로 다시 잰다.")
    print("\n  차이 = 추천 − 후보 전체 평균. **기준선은 후보 전체다** — 0 과 견주면"
          "\n  고르는 실력이 아니라 시장을 잰다."
          "\n  holdout 은 규칙 튜닝에 안 쓴 마지막 구간이다. 성적은 그쪽을 읽는다.")


# --- 잡음과 가르기 -------------------------------------------------------

def _block_means(values: np.ndarray, block: int, rounds: int, seed: int) -> np.ndarray:
    """**덩어리째** 다시 뽑은 표본의 평균 `rounds` 개.

    한 판씩 뽑으면 적중이 시간에 뭉쳐 다니는 성질이 사라져 귀무 세계가 실제보다
    깨끗해지고, 문턱이 너무 낮게 잡힌다. 이 저장소는 그걸로 한 번 답이 뒤집힌 적이
    있다(`scripts/metalabel.py`: 한 줄씩 섞으면 p=0, 덩어리째 섞으면 p=0.38).
    """
    rng = np.random.default_rng(seed)
    n = len(values)
    take = int(np.ceil(n / block))
    starts = rng.integers(0, n, size=(rounds, take))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    return values[idx.reshape(rounds, -1)[:, :n]].mean(axis=1)


def verdict(edges: list[float], full: list[float], rounds: int = 2000,
            seed: int = 20260830) -> dict:
    """이 차이가 0 과 구별되나.

    귀무는 "고르는 실력이 없다" = 차이 계열의 평균이 0 이다. 계열을 **평균만 빼서**
    0 으로 옮긴 뒤 덩어리째 다시 뽑아, 관측한 평균 이상이 얼마나 자주 나오는지 센다.

    **덩어리 길이는 손으로 고르지 않는다.** `overfit.pick_block` 이 Politis & White
    의 최적 길이로 정한다. 다만 그건 계열이 길어야 잴 수 있으므로 **전체 계열에서
    재서 holdout 에도 그대로 쓴다** — 12판으로 자기상관을 재면 그 값이 곧 잡음이다.
    """
    from marketlens.forecast import overfit

    values = np.asarray(edges, dtype="float64")
    values = values[np.isfinite(values)]
    if values.size < 4:
        return {"n": int(values.size), "mean": None}

    whole = np.asarray(full, dtype="float64")
    whole = whole[np.isfinite(whole)]
    # 흔들림이 없는 계열에서는 최적 블록 계산이 0 으로 나눈다. 그런 계열은 어떻게
    # 다시 뽑아도 평균이 같으므로 덩어리 길이가 답을 바꾸지 않는다 — 1 로 둔다.
    # **여기서 임의의 숫자를 넣으면 안 된다.** p 가 그 숫자 위에 서게 된다.
    block = 1 if whole.size < 8 or float(np.std(whole)) < 1e-12 else overfit.pick_block(whole)
    block = int(min(max(1, block), max(1, values.size // 4)))
    observed = float(values.mean())
    null = _block_means(values - observed, block, rounds, seed)
    spread = _block_means(values, block, rounds, seed + 1)
    return {
        "n": int(values.size), "block": int(block),
        "mean": round(observed, 4),
        # 한쪽 검정. "이만큼 좋은 게 우연히 나올 확률" 이다.
        "p": round(float(overfit.p_value(observed, null)), 4),
        "lo": round(float(np.percentile(spread, 2.5)), 4),
        "hi": round(float(np.percentile(spread, 97.5)), 4),
    }


def test(rows: list[dict]) -> None:
    """**표를 읽기 전에 이걸 본다.** 열다섯 칸 중 하나가 양수인 건 열다섯 번 재면
    으레 나오는 일이다. 부호가 아니라 p 와 구간을 봐야 한다."""
    if not rows:
        print("아직 백필한 게 없다")
        return
    print(f"\n{'시장':10s} {'지평':>5s} {'구간':>8s} {'판':>4s} {'차이':>9s} "
          f"{'95% 구간':>18s} {'p':>7s} {'덩어리':>6s}")
    cells: list[tuple[str, dict]] = []
    for provider in sorted({r["provider"] for r in rows}):
        for days in rec.DAYS:
            part = sorted((r for r in rows if r["provider"] == provider
                           and r["days"] == days), key=lambda r: r["origin"])
            if len(part) < 8:
                continue
            whole = [r["edgePct"] for r in part]
            for label, want in (("전체", None), ("holdout", True)):
                use = whole if want is None else [
                    r["edgePct"] for r in part if r.get("holdout")]
                got = verdict(use, whole)
                if got.get("mean") is None:
                    continue
                cells.append((f"{provider} {days}일 {label}", got))
                mark = "" if got["p"] > 0.05 else "  ←"
                print(f"{provider:10s} {days:4d}일 {label:>8s} {got['n']:4d} "
                      f"{got['mean']:+8.3f}%p  [{got['lo']:+6.2f}, {got['hi']:+6.2f}]"
                      f" {got['p']:7.3f} {got['block']:6d}{mark}")

    # **몇 칸을 봤는지 같이 낸다.** 이게 없으면 서른 칸 중 하나가 0.05 아래인 것을
    # 발견으로 읽게 되는데, 귀무에서도 제일 작은 p 는 대략 1/(칸+1) 근처에 온다.
    # 이 저장소는 "시험 횟수를 기록에 남긴다"를 두 곳에서 이미 지키고 있다.
    if cells:
        name, best = min(cells, key=lambda c: c[1]["p"])
        floor = 1 / (len(cells) + 1)
        print(f"\n  칸 {len(cells)}개를 봤다. 귀무에서도 제일 작은 p 는 대략 "
              f"{floor:.3f} 근처에 온다.")
        print(f"  제일 좋은 칸: {name} · p={best['p']:.3f} "
              f"· 95% 구간 [{best['lo']:+.2f}, {best['hi']:+.2f}]")
        if best["p"] >= floor:
            print("  → **우연이 으레 내놓는 정도다.** 발견으로 읽지 말 것.")
        else:
            print("  → 우연치고는 작다. 다만 구간이 0 을 품으면 여전히 아무 말도 아니다.")

    print("\n  p 는 '고르는 실력이 없다'는 가정에서 이만큼 좋은 평균이 우연히 나올 확률이다."
          "\n  결과를 **덩어리째** 다시 뽑아 잰다 — 한 판씩 뽑으면 적중이 시간에 뭉쳐"
          "\n  다니는 성질이 사라져 귀무 세계가 실제보다 깨끗해지고 문턱이 낮게 잡힌다."
          "\n  덩어리 길이는 Politis & White 최적값이다(손으로 고르지 않는다)."
          "\n  95% 구간이 0 을 품으면 부호는 아무 말도 아니다.")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", action="append")
    parser.add_argument("--origins", type=int, default=60)
    parser.add_argument("--every", type=int, default=RETRAIN_EVERY,
                        help="몇 origin 마다 다시 구울지")
    parser.add_argument("--summary", action="store_true", help="쌓인 것만 읽어 낸다")
    parser.add_argument("--test", action="store_true",
                        help="차이가 0 과 구별되는지 덩어리 부트스트랩으로 잰다")
    parser.add_argument("--restale", action="store_true",
                        help="지금 설정과 다른 설정으로 잰 줄을 버리고 다시 돌린다")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.test:
        test(read())
        return
    if args.summary:
        summary(read())
        test(read())
        return

    providers = args.provider or ["binance"]
    kept = read()
    if args.restale:
        setting = config(args.every)
        stale = [r for r in kept
                 if r.get("provider") in providers and r.get("config") != setting]
        if stale:
            print(f"설정이 바뀌어 버리는 줄: {len(stale)}개")
            kept = [r for r in kept if r not in stale]

    def save(provider: str, rows: list[dict]) -> None:
        nonlocal kept
        seen = {key(r) for r in rows}
        kept = [r for r in kept
                if not (r.get("provider") == provider and key(r) in seen)] + rows
        write(kept)

    for provider in providers:
        try:
            fresh = await run(provider, args.origins, args.every, args.restale,
                              None if args.dry_run else save)
        except Exception as exc:                                   # noqa: BLE001
            print(f"  {provider}: {type(exc).__name__} {str(exc)[:120]}")
            continue
        if args.dry_run:
            print(f"  (dry-run — {len(fresh)}줄을 저장하지 않았다)")
        elif fresh:
            print(f"  {len(fresh)}줄 · {OUT.relative_to(ROOT)}")

    summary(read())


if __name__ == "__main__":
    asyncio.run(main())
