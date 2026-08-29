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


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", action="append")
    parser.add_argument("--origins", type=int, default=60)
    parser.add_argument("--every", type=int, default=RETRAIN_EVERY,
                        help="몇 origin 마다 다시 구울지")
    parser.add_argument("--summary", action="store_true", help="쌓인 것만 읽어 낸다")
    parser.add_argument("--restale", action="store_true",
                        help="지금 설정과 다른 설정으로 잰 줄을 버리고 다시 돌린다")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.summary:
        summary(read())
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
