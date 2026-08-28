"""추천이 실제로 맞는지 재고, 그 결과를 저장한다.

추천 목록은 만들기 쉽고 검증하기 어렵다. 그래서 순서를 뒤집는다 — **먼저 재고,
잰 것만 쓴다.** 여기서 나온 `learning/factors.json` 이 없으면 `/api/screen` 은
순위를 만들지 않고 "아직 안 쟀다"고 답한다.

재는 것:

1. 후보 종목 전부의 팩터 시계열을 만든다 (학습 표와 같은 축, 같은 인과성 보증)
2. 시각마다 종목을 팩터로 줄 세우고, 그 뒤 실제 수익률 순위와 맞춘다 (랭크 IC)
3. 폴드로 나눠 **부호가 일관된 축만** 고른다
4. 고른 축으로 만든 점수가 상위/하위를 실제로 갈라놓는지 본다 (분위 스프레드)

**원값과 평소대비를 나눠서 잰다.** 원값 쪽(변동성·베타 등)은 IC 가 크게 나오지만
그건 "DOGE 는 원래 BTC 보다 많이 움직인다"는 고정 순위다. 늘 맞지만 오늘 뭘 볼지는
말해 주지 않는다. 오늘의 정보는 평소대비(`__rel`) 쪽에 있다.

돌리는 법:
    .venv/Scripts/python scripts/screen.py                    # 암호화폐+미국주식 일봉 1/2/3
    .venv/Scripts/python scripts/screen.py --provider yahoo --horizons 1 2 3 5
    .venv/Scripts/python scripts/screen.py --dry-run          # 저장 안 함
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import warnings
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from dotenv import load_dotenv                                    # noqa: E402

# 프로바이더가 키를 읽기 **전에** 올린다. 스크립트는 `api/app.py` 를 안 거치므로
# 여기서 직접 부르지 않으면 토스가 조용히 "키가 비어 있다"로 빠진다 — 실제로 그랬다.
load_dotenv(ROOT / ".env")
warnings.filterwarnings("ignore")
# 윈도우 콘솔은 기본이 cp949 라 한글 표에서 죽는다.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from marketlens import events as event_layer                      # noqa: E402
from marketlens.core.candle import closed_only                    # noqa: E402
from marketlens.core.timeframe import to_ms                       # noqa: E402
from marketlens.events.sources import attention                   # noqa: E402
from marketlens.forecast.ml import market                         # noqa: E402
from marketlens.providers import get as get_provider              # noqa: E402
from marketlens.screen import factors, ic, universe                # noqa: E402

# `daily.py` 와 같은 규칙. 저장소의 `learning/` 은 GitHub Actions 가 쓰고, 내 PC 는
# `MARKET_LENS_LEARNING=learning-local` 로 옆자리에 쓴다 — 둘이 같은 파일에 쓰면
# 매일 아침 pull 이 충돌한다.
LEARNING = Path(os.environ.get("MARKET_LENS_LEARNING") or ROOT / "learning")
if not LEARNING.is_absolute():
    LEARNING = ROOT / LEARNING
OUT = LEARNING / "factors.json"
BARS = {"1d": 3000, "1h": 12000, "15m": 12000, "1w": 1200}


async def load(provider: str, symbol: str, timeframe: str, bars: int):
    """시세·사건·관심도. 학습이 쓰는 것과 같은 조합이라 축이 어긋나지 않는다."""
    info = get_provider(provider).info
    try:
        df = await get_provider(provider).history(symbol, timeframe, bars)
    except Exception as exc:                                       # noqa: BLE001
        print(f"    {symbol}: 시세 실패 - {str(exc)[:60]}")
        return None
    closed = closed_only(df).reset_index(drop=True)
    if len(closed) < 400:
        print(f"    {symbol}: 봉이 {len(closed)}개뿐 - 건너뜀")
        return None
    found, _ = await event_layer.collect(df, symbol, info.market)
    relevant = event_layer.relevant(found, symbol, info.market)
    frame, _ = await attention.collect(closed, symbol)
    return symbol, closed, relevant, frame


def assemble(loaded: list, horizon: int) -> pd.DataFrame:
    """롱 포맷 표: 한 행이 (ts, symbol, 팩터들, fwd, fwd_abs).

    시장 계열은 종목 전부를 모아 한 번만 만든다 — 종목마다 따로 만들면 그 종목이
    곧 시장이 되어 상대강도가 항상 0 이 된다.
    """
    series = market.market_series({s: c for s, c, _, _ in loaded})
    frames = []
    for symbol, closed, events, attn in loaded:
        panel = factors.panel(closed, events, horizon=horizon,
                              attention_frame=attn,
                              market_frame=market.features(closed, series))
        if panel.empty:
            continue
        # 평소대비 축은 **종목별로** 만든다. 이어 붙인 뒤에 만들면 창이 종목 경계를
        # 넘어 굴러가 앞 종목의 값이 뒤 종목에 섞인다.
        panel = factors.with_relative(panel)
        fwd = factors.forward(closed, horizon).to_numpy()
        panel["symbol"] = symbol
        panel["fwd"] = fwd
        # "얼마나 움직일까"는 부호를 뗀 크기다. 방향과 섞으면 둘 다 흐려진다.
        panel["fwd_abs"] = np.abs(fwd)
        frames.append(panel)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("ts").reset_index(drop=True)


def cross_z(rows: pd.DataFrame, name: str) -> pd.Series:
    """시각마다의 횡단면 z. `rank.score` 와 같은 규칙이라 잰 것과 쓰는 것이 같다."""
    grouped = rows.groupby("ts")[name]
    std = grouped.transform("std").replace(0.0, np.nan)
    return (rows[name] - grouped.transform("mean")) / std


def spread(panel: pd.DataFrame, usable: dict, target: str, bucket: int = 3) -> dict:
    """점수가 상위/하위를 실제로 갈라놓는가.

    IC 는 "순위가 좀 맞는다"까지만 말한다. 쓰려면 **상위 묶음이 하위 묶음보다
    나았는지**를 봐야 하고, 그게 이 함수다.
    """
    if not usable:
        return {"available": False, "reason": "쓸 만한 축이 없다"}
    rows = panel.dropna(subset=[target]).copy()
    if rows.empty:
        return {"available": False, "reason": "라벨이 비었다"}

    total = pd.Series(0.0, index=rows.index)
    used = pd.Series(0, index=rows.index)
    for name, value in usable.items():
        if name not in rows.columns:
            continue
        z = cross_z(rows, name) * float(np.sign(value))
        total = total.add(z.fillna(0.0))
        used = used.add(z.notna().astype(int))
    rows["score"] = total / used.replace(0, np.nan)
    rows = rows.dropna(subset=["score"])
    if rows.empty:
        return {"available": False, "reason": "점수를 만들 수 없다"}

    # 시각마다 백분위로 바꿔 묶는다. 같은 시각 안에서만 비교해야 시장 전체가 오른
    # 날이 상위 묶음의 성적으로 둔갑하지 않는다.
    pct = rows.groupby("ts")["score"].rank(pct=True, method="first")
    rows["bucket"] = np.clip((pct * bucket).astype(int), 0, bucket - 1)
    means = rows.groupby("bucket")[target].mean()
    if len(means) < bucket:
        return {"available": False, "reason": "묶음을 나눌 만큼 종목이 없다"}
    return {
        "available": True,
        "buckets": [round(float(v) * 100, 4) for v in means],
        "topMinusBottomPct": round((float(means.iloc[-1]) - float(means.iloc[0])) * 100, 4),
        "rows": int(len(rows)),
    }


def report_horizon(panel: pd.DataFrame, scores: dict, folds: int) -> dict:
    """한 지평의 결과를 찍고 저장할 모양으로 만든다."""
    entry: dict = {}
    for kind in ("move", "direction"):
        target = "fwd_abs" if kind == "move" else "fwd"
        usable = {x.factor: x.ic for x in scores[kind] if x.usable}
        raw = {f: v for f, v in usable.items() if not f.endswith(factors.REL)}
        rel = {f: v for f, v in usable.items() if f.endswith(factors.REL)}
        entry[kind] = [asdict(x) for x in scores[kind][:14]]

        head = "변동" if kind == "move" else "방향"
        print(f"    [{head}] 쓸 만한 축 {len(usable)}개 "
              f"(원값 {len(raw)} / 평소대비 {len(rel)}) · 잰 축 {len(scores[kind])}개")
        for family, name in ((raw, "원값   "), (rel, "평소대비")):
            for x in [x for x in scores[kind] if x.factor in family][:3]:
                print(f"      {name} {factors.label(x.factor):26s} "
                      f"IC {x.ic:+.4f}  t {x.t_stat:+7.2f}  {x.fold_ic}")

        # 셋을 다 잰다: 전부 / 원값만 / 평소대비만. 나눠 보지 않으면 그냥 변동성
        # 순위를 추천이라고 부르게 된다.
        gaps = {"all": spread(panel, usable, target),
                "raw": spread(panel, raw, target),
                "relative": spread(panel, rel, target)}
        entry[f"{kind}Spread"] = gaps
        for key, gap in gaps.items():
            if gap.get("available"):
                print(f"      상위-하위({key:8s}) {gap['topMinusBottomPct']:+.3f}%"
                      f"  묶음별 {gap['buckets']}")
            else:
                print(f"      상위-하위({key:8s}) {gap.get('reason')}")
    return entry


async def run(provider: str, timeframe: str, horizons: list, folds: int,
              pause: float = 0.0) -> dict:
    symbols = universe.symbols(provider)
    if not symbols:
        print(f"  {provider}: 후보 목록이 없다")
        return {}
    print(f"\n{'=' * 78}\n{provider} · {timeframe} · {len(symbols)}종목\n{'=' * 78}")

    started = time.time()
    bars = BARS.get(timeframe, 3000)
    loaded = []
    for symbol in symbols:
        piece = await load(provider, symbol, timeframe, bars)
        if piece is not None:
            loaded.append(piece)
        # 토스·업비트는 호출 한도가 있다. 몰아치면 429 로 절반이 빠진다.
        if pause:
            await asyncio.sleep(pause)
    if len(loaded) < universe.MIN_BREADTH:
        print(f"  쓸 수 있는 종목이 {len(loaded)}개뿐 - 횡단면 순위가 안 된다")
        return {}
    print(f"  적재 {len(loaded)}종목 ({time.time() - started:.0f}s)")

    step_ms = to_ms(timeframe)
    result: dict = {}
    for horizon in horizons:
        panel = assemble(loaded, horizon)
        if panel.empty:
            print(f"  지평 {horizon}: 표를 못 만들었다")
            continue
        scores = ic.measure_all(panel, factors.all_candidates(), horizon * step_ms, folds)
        print(f"\n  --- 지평 {horizon}봉 ({len(panel):,}행) ---")
        result[str(horizon)] = report_horizon(panel, scores, folds)
    return result


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", action="append",
                        help="여러 번 줄 수 있다. 비우면 binance + yahoo")
    parser.add_argument("--timeframe", default="1d")
    parser.add_argument("--horizons", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--pause", type=float, default=0.0,
                        help="종목 사이 대기(초). 토스·업비트는 1.0 권장")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    providers = args.provider or ["binance", "yahoo"]
    stamp = pd.Timestamp.utcnow().isoformat(timespec="seconds")
    measured: dict[str, dict] = {}
    for provider in providers:
        found = await run(provider, args.timeframe, args.horizons, args.folds,
                          args.pause)
        if found:
            # **잰 봉을 프로바이더 항목 안에 적는다.** 최상위에 하나만 두면 시장마다
            # 다른 봉으로 잰 뒤에 무엇으로 잰 건지 알 수 없다. 화면이 이걸 대조한다.
            measured[provider] = {"timeframe": args.timeframe, "updated": stamp,
                                  "horizons": found}

    if args.dry_run:
        print("\n(dry-run - 저장하지 않았다)")
        return
    if not measured:
        print("\n잰 게 없다 — 저장하지 않는다. 옛 측정을 빈 파일로 덮으면 화면이 죽는다.")
        return

    # **덮지 말고 합친다.** 이번에 잰 프로바이더만 갈아끼우고 나머지는 그대로 둔다.
    # 통째로 덮으면 `--provider upbit` 한 번에 binance·yahoo 측정이 사라진다.
    payload = {"updated": stamp, "timeframe": args.timeframe,
               "minIc": ic.MIN_IC, "providers": {}}
    if OUT.is_file():
        try:
            payload = json.loads(OUT.read_text(encoding="utf-8"))
            payload["updated"] = stamp
            payload["minIc"] = ic.MIN_IC
        except (OSError, ValueError):
            print("  옛 파일을 못 읽었다 — 새로 쓴다")
    kept = [k for k in payload.setdefault("providers", {}) if k not in measured]
    payload["providers"].update(measured)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"\n저장: {OUT.relative_to(ROOT)} · 새로 잰 것 {sorted(measured)} · "
          f"그대로 둔 것 {sorted(kept)}")


if __name__ == "__main__":
    asyncio.run(main())
