"""아침 매수 추천 — 앞을 보고 기대 수익률을 낸 뒤 얼려 둔다.

전에 '추천' 이라 부르던 것은 **"얼마나 움직일까"의 순서**였다. 그건 사라는 뜻이 아니고,
종목 옆 숫자도 전날 등락률이라 지나간 값이었다. 여기서는 **모델이 앞으로를 보고 낸
기대 수익률**로 줄 세운다.

## 하루에 한 번, 아침에, 그리고 안 바뀐다

07:30 KST 에 한 번 뽑아 날짜별 파일로 얼린다. 같은 날 다시 돌려도 안 쓴다.
"안 바뀐다"를 캐시가 아니라 **데이터의 성질**로 만든다 — 누를 때마다 답이 달라지면
그건 추천이 아니라 시세 중계다.

## 절차 — 채점이 먼저다

```
① 지난 추천 채점   지평이 지난 과거 추천을 실제와 맞춘다
② 유니버스 적재    routes._symbol_data (시세+사건+관심도)
③ 풀링 학습 3회    시장마다 1·2·3일 모델 하나씩
④ 종목마다 예측    같은 모델에 df 만 바꿔 넣는다
⑤ 줄 세워 얼린다   기대 수익률 순 · 상위 3(사라) + 하위 2(피하라)
```

채점을 먼저 하는 이유: 뽑기가 터진 날에도 화면의 '지난 성적'은 최신이어야 한다.

**종목마다 학습하지 않는다.** `predict(df, name, …)` 는 심볼을 보지 않고, 목표값이
ATR 로 나눈 무차원이라 한 모델로 그 시장 종목 전부를 예측할 수 있다. 종목마다 구우면
쉰 번을 구워야 한다.

**대신 날짜마다 굽는다.** 지평 3 모델 하나로 1·2·3일을 다 뽑아 봤더니, 그 모델이
3일에서 기준선을 못 넘는 순간(skill 0.0013 < 0.002) `predict` 가 기준선만 쓰면서
1일·2일에 있던 우위(0.0054 · 0.0032)까지 같이 버렸다. 그러면 기대값이 그 종목 ATR 의
함수가 되어 **순위가 사실상 '변동성 순서'로 무너진다.** 지평마다 자기 성적으로
판정받게 두면, 되는 자리에서는 모델이 쓰이고 안 되는 자리에서는 기준선이 쓰인다.
안 쓰인 날은 `degenerate` 로 파일과 화면에 남는다.

**`study-*` 모델을 재사용하면 안 된다.** `scripts/study.py` 가 그 이름들을 과거 origin 에서
잘라 밤새 재학습한다 — 아침에 그걸 쓰면 며칠 전까지만 본 모델로 추천하게 된다.

## 이 추천이 얼마나 믿을 만한가

방향 예측은 27,664판에서 **55.0%** 였다(`docs/STUDY.md`). 횡단면 방향 스프레드는
하루 +0.03%p · 사흘 +0.65%p, 수수료 전이다. 그래서 **`scores.jsonl` 이 실제 성적을
쌓는다** — 몇 달 뒤 그 표가 이 기능의 진짜 답이다. 이기든 지든 그대로 남긴다.

돌리는 법:
    .venv/Scripts/python scripts/recommend.py
    .venv/Scripts/python scripts/recommend.py --dry-run --provider binance
    .venv/Scripts/python scripts/recommend.py --score-only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv                                    # noqa: E402

# 프로바이더가 키를 읽기 전에 올린다. 안 그러면 토스가 조용히 빠진다.
load_dotenv(ROOT / ".env")

from marketlens import events as event_layer                      # noqa: E402
from marketlens.api.routes import _symbol_data                    # noqa: E402
from marketlens.core.candle import closed_only                    # noqa: E402
from marketlens.forecast.ml import model as ml                    # noqa: E402
from marketlens.providers import get as get_provider              # noqa: E402
from marketlens.screen import universe                            # noqa: E402

# `daily.py`·`screen.py`·`study.py` 와 같은 규칙.
LEARNING = Path(os.environ.get("MARKET_LENS_LEARNING") or ROOT / "learning")
if not LEARNING.is_absolute():
    LEARNING = ROOT / LEARNING
OUT = LEARNING / "recommend"
SCORES = OUT / "scores.jsonl"

DAYS = (1, 2, 3)
BARS = 3000
BUY, AVOID = 3, 2
# 이 시장의 하루가 언제인지. 07:30 KST 는 22:30 UTC(전날)라, UTC 로 날짜를 지으면
# "오늘의 추천"에 어제 날짜가 뜬다.
CALENDAR = "Asia/Seoul"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today() -> str:
    return pd.Timestamp.now(tz=CALENDAR).strftime("%Y-%m-%d")


# --- 뽑기 ---------------------------------------------------------------

async def load_market(provider: str) -> tuple[list, list[dict]]:
    """그 시장의 후보 전부. 하나가 빠져도 나머지로 간다."""
    info = get_provider(provider).info
    loaded, skipped = [], []
    for symbol in universe.symbols(provider):
        try:
            data, _ = await _symbol_data(provider, symbol, "1d", BARS, info.market,
                                         event_layer.DEFAULT_SOURCES, True)
        except Exception as exc:                                   # noqa: BLE001
            skipped.append({"symbol": symbol, "reason": str(exc)[:120]})
            continue
        loaded.append(data)
        if provider.startswith(("toss", "upbit")):
            await asyncio.sleep(1.2)          # 호출 한도가 있다
    return loaded, skipped


def look(data, name: str, day: int) -> dict | None:
    """한 종목의 `day`일 뒤. 그 지평으로 구운 모델에서 최종 지점만 읽는다.

    **기대값을 `expectedMovePct` 대신 밴드에서 읽는다.** 기권 규칙이 켜지면 그 필드가
    비워지는데(`model.py`), 밴드는 일부러 남겨 두므로 이렇게 읽으면 그때도 순위가 난다.
    """
    try:
        out = ml.predict(data.df, name, data.events, "1d", data.attention)
    except Exception:                                              # noqa: BLE001
        return None
    if not out.get("available"):
        return None

    last = float(out["last"])
    mid, low, high = (out["bands"].get(k) or [] for k in ("p50", "p10", "p90"))
    if not mid or not low or not high:
        return None

    def pct(points) -> float:
        return round((points[-1]["value"] / last - 1) * 100, 3)

    atr = (float(out.get("atrPct") or 0.0) / 100) or None
    expected = pct(mid)
    return {
        "day": day, "expected": expected, "band": [pct(low), pct(high)],
        "probUp": None if out.get("probUp") is None else round(float(out["probUp"]), 3),
        "atrPct": out.get("atrPct"),
        # 모델이 얼마나 크게 움직인다고 했나. STUDY.md 는 이 값 상위 1/3 에서 방향
        # 적중이 64.7%(전체 55.0%)라고 말한다 — 정렬이 아니라 **확신도 표시**로만 쓴다.
        "moveAtr": round(expected / 100 / atr, 4) if atr else None,
        "last": last, "lastTs": int(out["lastTs"]),
        # **여기가 정직성의 핵심.** `volatility-baseline` 이면 모델을 하나도 안 쓴 것이고,
        # 그때 기대값은 그 종목 ATR 의 함수라 순위가 사실상 '변동성 순서'가 된다.
        "source": out.get("source"), "weight": out.get("weight"),
        "abstain": bool(out.get("abstain")),
    }


def confidence(values: list[float | None]) -> list[str]:
    """`moveAtr` 를 그날 그 시장 안에서 3분위로 갈라 라벨을 붙인다.

    **재정렬이 아니다.** `move_atr` 로 다시 줄 세우면 이 저장소가 이미 한 번 빠진
    함정을 되살린다 — `move_atr < 0.038 이면 기권` 이 최종 구간에서 크게 이겼는데
    알고 보니 음수 예측을 통째로 버리는 상승장 편향이었다(`docs/STUDY.md`).
    정렬은 기대 수익률로, 확신도는 라벨로만 둔다.
    """
    sizes = [abs(v) for v in values if v is not None]
    if len(sizes) < 3:
        return ["mid"] * len(values)
    low, high = np.quantile(sizes, [1 / 3, 2 / 3])
    out = []
    for v in values:
        size = None if v is None else abs(v)
        out.append("mid" if size is None
                   else "low" if size < low else "high" if size > high else "mid")
    return out


async def pick(provider: str) -> dict | None:
    """한 시장의 오늘 추천.

    **날짜마다 모델을 따로 굽는다.** 지평 3 하나로 1·2·3일을 다 뽑으면, 그 모델이
    3일에서 기준선을 못 넘는 순간(실제로 그랬다 — skill 0.0013) `predict` 가 기준선만
    쓰고 1일·2일에 있던 우위(0.0054·0.0032)까지 같이 버린다. 지평마다 자기 성적으로
    판정받게 두면 되는 자리에서는 모델이, 안 되는 자리에서는 기준선이 쓰인다.
    """
    started = time.time()
    loaded, skipped = await load_market(provider)
    if len(loaded) < universe.MIN_BREADTH:
        print(f"  {provider}: 시세를 받은 종목이 {len(loaded)}개뿐 — 오늘은 안 낸다")
        return None

    buyables = set(universe.buyable(provider))
    # 종목마다 날짜별 예측을 모은다.
    rows: dict[str, dict] = {}
    by_day: dict[str, dict] = {}

    for day in DAYS:
        name = f"recommend-{provider}-1d-{day}".lower()
        report, stale = {}, False
        try:
            report = ml.train(loaded, name, horizon=day, window=48, folds=3,
                              timeframe="1d")
        except Exception as exc:                                   # noqa: BLE001
            # 어제 구운 모델이 있으면 그걸 쓴다. 번들은 자족적이라 예측이 된다.
            # **조용히 쓰지 않는다** — 낡았다는 걸 파일과 화면에 남긴다.
            if ml.load(name) is None:
                print(f"  {provider} {day}일: 학습 실패 — {str(exc)[:60]}")
                continue
            stale, report = True, (ml.report(name) or {})

        found = {d.symbol: look(d, name, day) for d in loaded}
        found = {k: v for k, v in found.items() if v is not None and k in buyables}
        if len(found) < BUY + AVOID:
            print(f"  {provider} {day}일: 예측이 {len(found)}개뿐")
            continue

        order = sorted(found, key=lambda k: -found[k]["expected"])
        labels = confidence([found[k]["moveAtr"] for k in order])
        for symbol, label in zip(order, labels):
            found[symbol]["confidence"] = label
            row = rows.setdefault(symbol, {"symbol": symbol,
                                           "last": found[symbol]["last"],
                                           "lastTs": found[symbol]["lastTs"], "byDay": {}})
            row["byDay"][str(day)] = found[symbol]

        # **모델을 하나도 안 썼으면 그 사실을 남긴다.** 그때 순위는 사실상 변동성 순서다.
        degenerate = all(found[k]["source"] == "volatility-baseline" for k in order)
        by_day[str(day)] = {
            "buy": order[:BUY], "avoid": order[-AVOID:],
            "degenerate": degenerate,
            "allNegative": found[order[0]]["expected"] < 0,
            "learned": bool(report.get("learnedSomething")),
            "skill": (report.get("blendSkill") or {}).get(str(day)),
            "weight": (report.get("weights") or {}).get(str(day)),
            "modelStale": stale, "model": name,
        }
        mark = " · 모델 안 씀(사실상 변동성 순서)" if degenerate else ""
        print(f"  {provider} {day}일: {order[0]} {found[order[0]]['expected']:+.2f}% "
              f"· skill {by_day[str(day)]['skill']}{mark}")

    if not by_day:
        return None

    closed = closed_only(loaded[0].df)
    last_ts = int(closed["ts"].iloc[-1])
    last_day = pd.Timestamp(last_ts, unit="ms", tz="UTC")
    stale_bars = (pd.Timestamp(today(), tz=CALENDAR).tz_convert("UTC").normalize()
                  - last_day.normalize()).days - 1

    print(f"  {provider}: 후보 {len(rows)}종목 · {time.time() - started:.0f}s")
    return {
        "provider": provider, "lastTs": last_ts, "basedOn": f"{last_day:%Y-%m-%d}",
        "staleBars": max(0, int(stale_bars)),
        "candidates": list(rows.values()), "byDay": by_day, "skipped": skipped,
    }


# --- 지난 추천 채점 ------------------------------------------------------

def already() -> set[str]:
    """이미 채점한 (날짜, 시장, 일수). **재실행이 정상인 스크립트라 이게 없으면
    표본이 두 배가 되고 모든 비율이 조용히 좋아진다.**"""
    if not SCORES.is_file():
        return set()
    done = set()
    for line in SCORES.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        done.add(f"{row['date']}:{row['provider']}:{row['days']}")
    return done


async def score_one(frozen: dict, provider: str, body: dict, days: int) -> dict | None:
    """그 추천이 실제로 어땠나. 아직 결과가 안 나왔으면 None(실패가 아니다).

    **`lastTs` 에 앵커한다, 파일 날짜가 아니라.** 그게 예측이 딛고 선 마지막 확정봉이고
    예측의 유일한 원점이다. 날짜로 맞추면 공휴일 하나에 조용히 밀린다.
    """
    anchor = int(body.get("lastTs") or 0)
    wanted = [r["symbol"] for r in body.get("candidates", [])]
    if not anchor or not wanted:
        return None

    real: dict[str, float] = {}
    for symbol in wanted:
        try:
            df = await get_provider(provider).history(symbol, "1d", 60)
        except Exception:                                          # noqa: BLE001
            continue
        closed = closed_only(df).reset_index(drop=True)
        stamps = closed["ts"].to_numpy()
        where = int(np.searchsorted(stamps, anchor, side="right")) - 1
        if where < 0 or where + days >= len(closed):
            return None                       # 아직 그날로부터 days 봉이 안 지났다
        start = float(closed["close"].iloc[where])
        real[symbol] = (float(closed["close"].iloc[where + days]) / start - 1) * 100
        if provider.startswith(("toss", "upbit")):
            await asyncio.sleep(1.2)

    if len(real) < universe.MIN_BREADTH:
        return None
    buys = [real[r["symbol"]] for r in body["buy"] if r["symbol"] in real]
    avoids = [real[r["symbol"]] for r in body["avoid"] if r["symbol"] in real]
    everyone = list(real.values())
    if not buys:
        return None

    buy_mean, all_mean = float(np.mean(buys)), float(np.mean(everyone))
    # 밴드가 맞았나. 방향과 달리 밴드는 실제로 잘 맞는다(전체 82.2%).
    inside = []
    for row in body["candidates"]:
        band = ((row.get("byDay") or {}).get(str(days)) or {}).get("band")
        value = real.get(row["symbol"])
        if band and value is not None and band[0] <= value <= band[1]:
            inside.append(row)
    return {
        "date": frozen["date"], "provider": provider, "days": days,
        "basedOn": body.get("basedOn"), "scoredAt": now(),
        "buyPct": round(buy_mean, 4),
        "avoidPct": round(float(np.mean(avoids)), 4) if avoids else None,
        # **기준선은 후보 전체 평균이다.** 0 과 견주면 고르는 실력이 아니라 시장을 잰다.
        "universePct": round(all_mean, 4),
        "edgePct": round(buy_mean - all_mean, 4),
        "bandHit": round(len(inside) / len(real), 4),
        "n": len(real),
    }


async def settle() -> list[dict]:
    done = already()
    fresh: list[dict] = []
    for path in sorted(OUT.glob("20*.json")):
        try:
            frozen = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for provider, body in (frozen.get("providers") or {}).items():
            for days in DAYS:
                if f"{frozen['date']}:{provider}:{days}" in done:
                    continue
                row = await score_one(frozen, provider, body, days)
                if row is not None:
                    fresh.append(row)
    return fresh


def summary() -> dict:
    """누적 성적. 시장을 섞지 않는다 — 암호화폐와 국내주식은 변동성이 달라
    합치면 평균이 아무 뜻이 없다."""
    if not SCORES.is_file():
        return {"count": 0, "byProvider": {}}
    rows = []
    for line in SCORES.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    out: dict = {"count": len(rows), "byProvider": {}}
    for provider in sorted({r["provider"] for r in rows}):
        per = {}
        for days in DAYS:
            part = [r for r in rows if r["provider"] == provider and r["days"] == days]
            if not part:
                continue
            per[str(days)] = {
                "n": len(part),
                "buyPct": round(float(np.mean([r["buyPct"] for r in part])), 4),
                "universePct": round(float(np.mean([r["universePct"] for r in part])), 4),
                "edgePct": round(float(np.mean([r["edgePct"] for r in part])), 4),
                "winRate": round(float(np.mean([r["edgePct"] > 0 for r in part])), 4),
                "bandHit": round(float(np.mean([r["bandHit"] for r in part])), 4),
            }
        if per:
            out["byProvider"][provider] = per
    return out


# --- 돌리기 --------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", action="append",
                        help="여러 번 줄 수 있다. 비우면 키가 있는 시장 전부")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="오늘 파일이 있어도 다시 뽑는다")
    parser.add_argument("--score-only", action="store_true")
    parser.add_argument("--pick-only", action="store_true")
    args = parser.parse_args()

    providers = args.provider or [p for p in universe.providers()
                                  if p not in ("kis", "csv", "us_stock")]
    date = today()
    path = OUT / f"{date}.json"

    # **채점이 먼저다.** 뽑기가 터진 날에도 '지난 성적'은 최신이어야 한다.
    if not args.pick_only and not args.dry_run:
        fresh = await settle()
        if fresh:
            OUT.mkdir(parents=True, exist_ok=True)
            with SCORES.open("a", encoding="utf-8") as handle:
                for row in fresh:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"지난 추천 {len(fresh)}건 채점")
            for row in fresh[-6:]:
                print(f"  {row['date']} {row['provider']:9s} {row['days']}일 · "
                      f"추천 {row['buyPct']:+.2f}% · 후보평균 {row['universePct']:+.2f}% "
                      f"· 차이 {row['edgePct']:+.2f}%p")
        found = summary()
        for provider, per in found.get("byProvider", {}).items():
            for days, part in per.items():
                mark = "" if part["n"] >= 30 else f"  (아직 {part['n']}일치 — 30일은 있어야 성적이다)"
                print(f"  누적 {provider:9s} {days}일: 차이 {part['edgePct']:+.2f}%p · "
                      f"이긴 비율 {part['winRate'] * 100:.0f}%{mark}")

    if args.score_only:
        return
    if path.is_file() and not args.force and not args.dry_run:
        print(f"\n오늘({date}) 추천은 이미 있다: {path.relative_to(ROOT)}")
        return

    print(f"\n아침 추천 · {date} · 시장 {len(providers)}개")
    picked: dict[str, dict] = {}
    for provider in providers:
        try:
            one = await pick(provider)
        except Exception as exc:                                   # noqa: BLE001
            print(f"  {provider}: {type(exc).__name__} {str(exc)[:70]}")
            continue
        if one:
            picked[provider] = one

    if args.dry_run:
        print("\n(dry-run — 저장하지 않았다)")
        return
    if not picked:
        print("\n낼 게 없다 — 파일을 만들지 않는다")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"date": date, "generatedAt": now(), "timeframe": "1d", "days": list(DAYS),
         "providers": picked}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n얼렸다: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
