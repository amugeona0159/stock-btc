"""as-of 검증 — "그날 그 자리에 서서 예측하고, 나중에 실제와 맞춰 본다".

학습 성적표(pinball skill)는 "분위수를 잘 맞혔나"를 재지만, 사람이 알고 싶은 건
**"그래서 실제로 얼마나 맞았나"** 다. 이 파일이 그걸 잰다.

절차는 하나뿐이다:
1. 기준 시각(origin)을 정한다
2. **그 시각 이전 데이터만 잘라** 예측을 만든다
3. 지평만큼 지난 뒤의 실제 경로와 맞춰 본다

**이 파일의 전부는 미래 차단이다.** origin 이후가 새면 성적이 예뻐지고, 그 예쁜
숫자를 믿게 된다. 자르는 곳이 넷이다 — 시세·사건·유사구간 후보·학습 표.
`tests/test_asof.py` 가 "origin 뒤 데이터를 바꿔도 그 시점 예측이 안 변한다"를 지킨다.

**학습은 동료 종목까지 모아서 한다**(`--peers`, 기본 12). 한 종목만 쓰면 표본이 몇천
행이라 실력을 실제보다 낮게 재게 된다 — `scripts/sweep.py` 가 잰 게 정확히 그거였다.
동료도 origin 마다 같이 잘린다. 하나라도 안 자르면 그 종목의 미래가 학습 표로 새서
이 파일이 재는 것이 아무 뜻도 없어진다.

돌리는 법:
    .venv/Scripts/python scripts/asof.py --tf 1d --horizon 10
    .venv/Scripts/python scripts/asof.py --tf 1h --horizon 24 --origins 40
    .venv/Scripts/python scripts/asof.py --tf 1d --peers 0    # 예전처럼 한 종목만
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
warnings.filterwarnings("ignore")

from marketlens import events as event_layer  # noqa: E402
from marketlens.analog import matcher, projection  # noqa: E402
from marketlens.core.candle import closed_only  # noqa: E402
from marketlens.core.timeframe import to_ms  # noqa: E402
from marketlens.events.sources import attention  # noqa: E402
from marketlens.forecast.ml import model as ml  # noqa: E402
from marketlens.providers import get as get_provider  # noqa: E402
from marketlens.screen import universe  # noqa: E402

WINDOW = 48
# 학습을 몇 개 origin 마다 다시 할지. 매번 하면 너무 비싸고, 한 번만 하면 미래를 본다.
RETRAIN_EVERY = 8
# origin 이 이 봉 수보다 앞이면 건너뛴다. 지표와 유사구간이 데워질 자리가 필요하다.
MIN_HISTORY = 600


@dataclass
class Slice:
    """origin 시점의 '그때까지의 세계'. 여기 담긴 것 말고는 아무것도 못 본다."""

    symbol: str
    closed: pd.DataFrame
    events: list
    attention: pd.DataFrame


def cut(full: Slice, origin_ts: int) -> Slice:
    """origin 이후를 전부 잘라 낸다. 네 갈래 모두.

    `scripts/backfill.py` 에도 같은 일을 하는 `cut` 이 있다. 그쪽은 `SymbolData`
    (시세에 미확정봉이 섞인 `df`)를 받고 여기는 `Slice`(확정봉만)를 받아서 하나로
    합치지 않았다 — **둘 다 맞아야 하므로 한쪽을 고치면 다른 쪽도 본다.**
    """
    mask = full.closed["ts"].to_numpy() <= origin_ts
    closed = full.closed[mask].reset_index(drop=True)
    return Slice(
        symbol=full.symbol,
        closed=closed,
        events=[e for e in full.events if e.ts <= origin_ts],
        attention=full.attention[mask].reset_index(drop=True)
        if full.attention is not None and len(full.attention) == len(mask) else None,
    )


@dataclass
class Outcome:
    origin_ts: int
    horizon: int
    realised: np.ndarray          # 실제 누적 로그수익률 경로 (0에서 시작)
    predicted: np.ndarray         # 예측 중앙 경로 (같은 형태)
    low: float                    # 지평에서의 p10 (로그수익률)
    high: float                   # 지평에서의 p90
    atr: float
    source: str

    @property
    def realised_final(self) -> float:
        return float(self.realised[-1])

    @property
    def predicted_final(self) -> float:
        return float(self.predicted[-1])


def dtw_norm(a: np.ndarray, b: np.ndarray) -> float:
    """정규화 DTW 거리. 두 경로의 모양이 얼마나 닮았나 — 크기 차이는 빼고 본다."""
    a, b = np.asarray(a, dtype="float64"), np.asarray(b, dtype="float64")
    span = max(np.ptp(a), np.ptp(b), 1e-12)
    return matcher.dtw_distance(a / span, b / span, band=0.2)


def score(outcomes: list[Outcome], label: str) -> dict:
    """사람이 읽는 지표. 전부 기준선과 나란히 봐야 의미가 있다."""
    if not outcomes:
        return {"label": label, "count": 0}

    realised = np.array([o.realised_final for o in outcomes])
    predicted = np.array([o.predicted_final for o in outcomes])
    atr = np.array([o.atr for o in outcomes])
    inside = np.array([o.low <= o.realised_final <= o.high for o in outcomes])

    # 방향은 실제가 뚜렷하게 움직인 경우만 센다 — 0 근처에서는 부호가 동전던지기다.
    # 예측이 늘 0인 기준선은 방향을 말한 적이 없다. 0%가 아니라 '없음'이다.
    moved = np.abs(realised) > 0.25 * atr
    spoke = np.abs(predicted) > 1e-12
    direction = (np.sign(predicted) == np.sign(realised))[moved & spoke]

    path_corr, path_dtw = [], []
    for o in outcomes:
        if np.std(o.predicted) > 1e-12 and np.std(o.realised) > 1e-12:
            path_corr.append(float(np.corrcoef(o.predicted, o.realised)[0, 1]))
        path_dtw.append(dtw_norm(o.predicted, o.realised))

    return {
        "label": label,
        "count": len(outcomes),
        "bandHit": float(inside.mean()),
        "directionHit": float(direction.mean()) if direction.size else float("nan"),
        "directionN": int(direction.size),
        # 동전던지기 대비 몇 %p 나은지. 방향 적중은 이 값으로 읽어야 한다.
        "directionEdge": (float(direction.mean() - 0.5) if direction.size else float("nan")),
        "pathCorr": float(np.mean(path_corr)) if path_corr else float("nan"),
        "pathDtw": float(np.mean(path_dtw)),
        # 오차를 ATR 로 나눠 둔다. 종목·기간이 달라도 이 숫자는 비교된다.
        "errorAtr": float(np.mean(np.abs(predicted - realised) / atr)),
        "medianErrorPct": float(np.median(np.abs(np.expm1(predicted) - np.expm1(realised))) * 100),
    }


def show(rows: list[dict]) -> None:
    print(f"\n  {'':22s} {'표본':>5s} {'밴드적중':>8s} {'방향적중':>8s} "
          f"{'경로상관':>8s} {'DTW':>7s} {'오차/ATR':>9s} {'오차%':>7s}")
    for r in rows:
        if not r.get("count"):
            print(f"  {r['label']:20s} 표본 없음")
            continue
        band = f"{r['bandHit'] * 100:6.1f}%"
        direction = ("  —  " if np.isnan(r["directionHit"])
                     else f"{r['directionHit'] * 100:5.1f}%({r['directionN']})")
        corr = "  —  " if np.isnan(r["pathCorr"]) else f"{r['pathCorr']:+7.3f}"
        print(f"  {r['label']:20s} {r['count']:5d} {band:>8s} {direction:>12s} "
              f"{corr:>8s} {r['pathDtw']:7.3f} {r['errorAtr']:9.2f} "
              f"{r['medianErrorPct']:6.2f}%")


async def load(symbol: str, provider: str, timeframe: str, bars: int,
               market_name: str) -> Slice | None:
    try:
        df = await get_provider(provider).history(symbol, timeframe, bars)
    except Exception as exc:  # noqa: BLE001
        print(f"  {symbol}: 시세 실패 — {str(exc)[:70]}")
        return None
    closed = closed_only(df).reset_index(drop=True)
    found, _ = await event_layer.collect(df, symbol, market_name)
    relevant = event_layer.relevant(found, symbol, market_name)
    frame, _ = await attention.collect(closed, symbol)
    return Slice(symbol, closed, relevant, frame)


def analog_forecast(view: Slice, horizon: int, timeframe: str) -> dict | None:
    """유사구간 예측 — 그 시점까지의 데이터만 본다."""
    if len(view.closed) < WINDOW + horizon + 60:
        return None
    matches = matcher.search(view.closed, [matcher.Series(view.symbol, view.closed)],
                             window=WINDOW, horizon=horizon, top_k=20)
    if not matches:
        return None
    return projection.project(view.closed, matches, horizon, timeframe)


def to_log_path(points: list[dict], last: float) -> np.ndarray:
    return np.log(np.array([p["value"] for p in points], dtype="float64") / last)


async def run(symbol: str, provider: str, market_name: str, timeframe: str,
              horizon: int, bars: int, origins: int,
              peers: list[Slice] | None = None) -> None:
    """`peers` 는 **학습에만** 쓰는 동료 종목이다(첫 번째가 이 종목 자신).

    한 종목만으로 학습하면 표본이 몇천 행이라 실력을 실제보다 낮게 재게 된다.
    `scripts/sweep.py` 가 잰 것이 정확히 그거였다 — 같은 코드가 6종목 4천봉에서
    skill −0.11, 12종목 1.2만봉에서 +0.010 이었다. **"시간봉은 안 된다"가 아니라
    "몇천 행으로는 안 된다"였다.** 여기서 한 종목만 쓰면 그 함정을 다시 판다.

    예측은 그대로 이 종목의 잘린 시세로만 한다. 동료는 학습 표를 키울 뿐이다.
    """
    full = await load(symbol, provider, timeframe, bars, market_name)
    if full is None:
        return
    pool = [full] + [p for p in (peers or []) if p.symbol != full.symbol]
    closed = full.closed
    step = to_ms(timeframe)
    n = len(closed)
    if n < MIN_HISTORY + horizon + 50:
        print(f"  {symbol}: 봉이 모자라 건너뛴다 ({n})")
        return

    # origin 은 '충분한 과거'와 '결과를 아는 미래' 사이에서 고르게 잡는다.
    lo, hi = MIN_HISTORY, n - horizon - 1
    positions = np.unique(np.linspace(lo, hi, origins).astype(int))
    log_close = np.log(closed["close"].to_numpy(dtype="float64"))

    analog_out: list[Outcome] = []
    learned_out: list[Outcome] = []
    baseline_out: list[Outcome] = []
    model_name = f"asof-{symbol}-{timeframe}".lower()
    trained_at = -10**9

    print(f"\n  {symbol} {timeframe} · 지평 {horizon}봉 · origin {len(positions)}개")
    for order, position in enumerate(positions):
        origin_ts = int(closed["ts"].iloc[position])
        view = cut(full, origin_ts)
        realised = log_close[position : position + horizon + 1] - log_close[position]
        atr = float(ml.volatility_scale(view.closed).iloc[-1])
        if not np.isfinite(atr) or atr <= 0:
            continue

        # --- 유사구간 ---
        forecast = analog_forecast(view, horizon, timeframe)
        if forecast and forecast.get("available"):
            last = forecast["last"]
            analog_out.append(Outcome(
                origin_ts, horizon, realised,
                to_log_path(forecast["bands"]["p50"], last),
                float(np.log(forecast["bands"]["p10"][-1]["value"] / last)),
                float(np.log(forecast["bands"]["p90"][-1]["value"] / last)),
                atr, "analog",
            ))

        # --- 학습 (origin 이전 데이터로만 학습한다) ---
        if order - trained_at >= RETRAIN_EVERY:
            try:
                # **동료도 같이 자른다.** 하나라도 안 자르면 그 종목의 미래가
                # 학습 표로 새고, 그러면 이 파일이 재는 것이 아무 뜻도 없어진다.
                ml.train([ml.SymbolData(s.symbol, s.closed, s.events, s.attention)
                          for s in (cut(p, origin_ts) for p in pool)],
                         model_name, horizon=horizon, window=WINDOW, folds=3,
                         timeframe=timeframe)
                trained_at = order
            except Exception:  # noqa: BLE001 - 표본이 모자란 초반 origin 은 그냥 건너뛴다
                pass
        if ml.load(model_name) is not None:
            out = ml.predict(view.closed, model_name, view.events, timeframe, view.attention)
            if out.get("available"):
                last = out["last"]
                learned_out.append(Outcome(
                    origin_ts, horizon, realised,
                    to_log_path(out["bands"]["p50"], last),
                    float(np.log(out["bands"]["p10"][-1]["value"] / last)),
                    float(np.log(out["bands"]["p90"][-1]["value"] / last)),
                    atr, out.get("source", "?"),
                ))

        # --- 기준선: 방향 없음(중앙 0), 폭은 현재 변동성 × √시간 ---
        baseline_out.append(Outcome(
            origin_ts, horizon, realised, np.zeros(horizon + 1),
            float(-1.2816 * atr * np.sqrt(horizon)),   # 정규 10% 분위
            float(+1.2816 * atr * np.sqrt(horizon)),
            atr, "baseline",
        ))

    show([
        score(baseline_out, "기준선 (변동성만)"),
        score(analog_out, "유사구간"),
        score(learned_out, "학습"),
    ])


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", default="1d")
    parser.add_argument("--horizon", type=int, default=10)
    parser.add_argument("--origins", type=int, default=30)
    parser.add_argument("--bars", type=int, default=3000)
    parser.add_argument("--symbols", default="")
    # 학습 동료 수. 0 이면 예전처럼 한 종목만 쓴다 — 비교용으로 남긴다.
    parser.add_argument("--peers", type=int, default=12)
    args = parser.parse_args()

    default = {
        "1d": [("BTCUSDT", "binance", "crypto"), ("ETHUSDT", "binance", "crypto"),
               ("AAPL", "yahoo", "us"), ("^GSPC", "yahoo", "us")],
        "1h": [("BTCUSDT", "binance", "crypto"), ("ETHUSDT", "binance", "crypto"),
               ("SOLUSDT", "binance", "crypto")],
    }.get(args.tf, [("BTCUSDT", "binance", "crypto")])
    if args.symbols:
        default = [(s.strip(), "binance", "crypto") for s in args.symbols.split(",")]

    print(f"as-of 검증 · {args.tf} · 지평 {args.horizon}봉 · origin {args.origins}개/종목")
    print("  밴드적중: 실제가 p10~p90 안에 든 비율 (목표 80%)")
    print("  방향적중: 실제가 0.25 ATR 이상 움직인 경우만")
    print("  DTW: 경로 모양 거리 (작을수록 닮음) · 오차/ATR: 변동성 단위 오차")

    # **동료 종목을 시장마다 한 번만 받아 돌려 쓴다.** 종목마다 다시 받으면 같은
    # 시세를 열두 번씩 내려받게 된다. 여기 담긴 것은 **학습 표를 키우는 데만** 쓰이고,
    # origin 마다 다시 잘려서 들어간다(`run` 참조).
    pools: dict[str, list[Slice]] = {}
    for _, provider, market_name in default:
        if provider in pools or args.peers == 0:
            continue
        wanted = [s for s in universe.symbols(provider)][:args.peers]
        loaded = []
        for peer in wanted:
            got = await load(peer, provider, args.tf, args.bars, market_name)
            if got is not None:
                loaded.append(got)
        pools[provider] = loaded
        print(f"\n  {provider}: 학습 동료 {len(loaded)}종목")

    for symbol, provider, market_name in default:
        await run(symbol, provider, market_name, args.tf, args.horizon,
                  args.bars, args.origins, pools.get(provider))


if __name__ == "__main__":
    asyncio.run(main())
