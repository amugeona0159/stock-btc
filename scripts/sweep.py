"""학습 기법 실험 장치.

"이 기법이 실제로 도움이 되는가" 를 재는 곳이다. 모델 코드에 기법을 먼저 넣고
나중에 재면, 안 되는 기법이 그대로 남는다 — 여기서 재고 이긴 것만 옮긴다.

돌리는 법:
    .venv/Scripts/python scripts/sweep.py            # 전체 (40~60분)
    .venv/Scripts/python scripts/sweep.py --quick    # 짧게

모든 비교는 **같은 검증 구간, 같은 손실**로 한다. 기준선을 바꿔 가며 이기는 것은
이기는 게 아니다.

판정은 `blendSkill` — 변동성 기준선과 섞은 결과가 그 기준선을 넘는가. 섞는 비중은
**앞 폴드에서 고른 값을 다음 폴드에 쓴다**(검증 구간에서 고르면 자기 답을 본 성적이다).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# 콘솔이 cp949 라 ✓·— 같은 글자에서 죽는다. 다른 스크립트와 같은 처리.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
warnings.filterwarnings("ignore")

from marketlens import events as event_layer  # noqa: E402
from marketlens.core.candle import closed_only  # noqa: E402
from marketlens.events.sources import attention  # noqa: E402
from marketlens.forecast.ml import dataset, market, volatility  # noqa: E402
from marketlens.forecast.ml import model as ml  # noqa: E402
from marketlens.providers import get as get_provider  # noqa: E402

QUANTILES = (0.1, 0.5, 0.9)
FOLDS = 4
WINDOW = 48

TREE = dict(max_iter=80, learning_rate=0.03, max_depth=2,
            min_samples_leaf=250, l2_regularization=20.0, max_features=0.4,
            random_state=1)


@dataclass
class Spec:
    symbol: str
    provider: str
    market: str = "crypto"


CRYPTO = [Spec(s, "binance") for s in (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "LTCUSDT", "TRXUSDT",
)]
STOCKS = [Spec(s, "yahoo", "us") for s in
          ("AAPL", "MSFT", "NVDA", "AMZN", "META", "^GSPC")]


@dataclass
class Loaded:
    symbol: str
    closed: pd.DataFrame
    events: list
    attention: pd.DataFrame


async def load(spec: Spec, timeframe: str, bars: int) -> Loaded | None:
    """시세·사건·관심도를 한 번만 받아 둔다. 지평이 바뀌어도 이건 그대로다."""
    try:
        df = await get_provider(spec.provider).history(spec.symbol, timeframe, bars)
    except Exception as exc:  # noqa: BLE001
        print(f"    {spec.symbol}: 시세 실패 — {str(exc)[:70]}")
        return None
    closed = closed_only(df).reset_index(drop=True)
    if len(closed) < 600:
        return None
    found, _ = await event_layer.collect(df, spec.symbol, spec.market)
    relevant = event_layer.relevant(found, spec.symbol, spec.market)
    frame, _ = await attention.collect(closed, spec.symbol)
    return Loaded(spec.symbol, closed, relevant, frame)


# 루트를 씌우면 편의가 생긴다. E[V]=σ² 여도 E[√V]≠σ 다 — 밴드 폭은 σ 단위라
# 이 보정이 그대로 폭에 걸린다. 종가 제곱평균은 25% 나 낮게 나오는데(√(π/2)),
# 범위 추정량은 3~4% 다. 출처: Molnár (2012), doi:10.1016/j.irfa.2011.06.012
SQRT_BIAS = {"gk": 1.034, "parkinson": 1.043, "rs": 1.043}


def _scale_for(closed: pd.DataFrame, kind: str, hourly: bool) -> np.ndarray:
    """목표값을 나눌 변동성 자.

    **`skill` 로 이 셋을 비교하면 안 된다.** 기준선(`base_pred`)도 이 자를 곱해서
    만들어지므로 자를 바꾸면 분모가 같이 움직인다. 실제 비교는 절대 손실로 한다 —
    `truth` 는 자와 무관하게 늘 원래 수익률이라 그쪽은 공평하다.
    """
    if kind == "atr":
        return ml.volatility_scale(closed, hourly=hourly).to_numpy()
    if kind == "gk":
        series = volatility.garman_klass(closed) * SQRT_BIAS["gk"]
    elif kind == "parkinson":
        series = volatility.parkinson(closed) * SQRT_BIAS["parkinson"]
    else:
        raise ValueError(kind)
    return series.replace(0.0, np.nan).to_numpy()


def assemble(items: list[Loaded], horizon: int, *, use_market: bool,
             hourly_scale: bool, residual: bool,
             use_range: bool = False,
             scale_kind: str = "atr") -> tuple[pd.DataFrame, list[str]]:
    """지평 하나에 대한 학습 표. 시장 계열은 종목 전부를 모아 한 번만 만든다."""
    series = market.market_series({i.symbol: i.closed for i in items}) if use_market \
        else pd.DataFrame(columns=["ts", "market_ret"])

    frames = []
    for item in items:
        market_frame = market.features(item.closed, series) if use_market else None
        panel = dataset.build(item.closed, item.events, window=WINDOW, horizon=horizon,
                              attention_frame=item.attention, market_frame=market_frame)
        if panel.empty:
            continue
        scale = _scale_for(item.closed, scale_kind, hourly_scale)
        y = dataset.forward_return(item.closed, horizon).to_numpy()
        if residual:
            # 시장 공통 움직임을 목표에서 뺀다 — 알파만 예측하는 셈이다.
            # **진단용이다.** 값을 쓰려면 시장 예측을 따로 더해야 한다.
            beta = np.tanh(panel["beta"].to_numpy()) + 1.0
            y = y - beta * market.forward(series, item.closed, horizon).to_numpy()

        frame = panel[list(dataset.FEATURE_COLUMNS)].copy()
        if use_range:
            # 봉 범위로 잰 변동성. `dataset.build` 와 같은 확정봉 인덱스라 그대로 붙는다.
            ranges = volatility.range_features(item.closed)
            for column in volatility.RANGE_COLUMNS:
                frame[column] = ranges[column].to_numpy()
        frame["ts"] = panel["ts"]
        frame["scale"] = scale
        frame["y"] = y / scale
        # 합성 평가에 필요한 것들. 잔차로 학습하고 **총수익률**로 채점하려면
        # 베타와 시장 미래 수익률, 그리고 원래 총수익률이 다 있어야 한다.
        if residual:
            frame["beta_used"] = beta
            # **알파와 같은 단위로** 둔다. 알파는 ATR 로 나눠 뒀는데 시장만 원단위로
            # 두면 합성에서 폭이 수십 배로 벌어진다.
            frame["market_fwd"] = market.forward(series, item.closed, horizon).to_numpy() / scale
            frame["y_total"] = dataset.forward_return(item.closed, horizon).to_numpy() / scale
        frames.append(frame)

    if not frames:
        return pd.DataFrame(), []
    pooled = pd.concat(frames, ignore_index=True)
    pooled = pooled.replace([np.inf, -np.inf], np.nan).dropna()
    columns = list(dataset.FEATURE_COLUMNS)
    if use_range:
        columns += list(volatility.RANGE_COLUMNS)
    return pooled.sort_values("ts").reset_index(drop=True), columns


def evaluate(pooled: pd.DataFrame, columns: list[str], horizon_ms: int,
             composed: bool = False) -> dict:
    from sklearn.ensemble import HistGradientBoostingRegressor

    X = pooled[columns].to_numpy(dtype="float64")
    y = pooled["y"].to_numpy(dtype="float64")
    scale = pooled["scale"].to_numpy(dtype="float64")
    folds = ml.time_folds(pooled["ts"].to_numpy(), horizon_ms, FOLDS)
    if not folds:
        return {"skill": float("nan"), "blend": float("nan"), "weight": 0.0,
                "loss": float("nan"), "baseLoss": float("nan"), "rows": len(pooled)}

    # 합성 채점이면 목표도 기준선도 **총수익률**이다 — 그래야 이전 숫자들과 비교된다.
    total = pooled["y_total"].to_numpy(dtype="float64") if composed else y
    beta_used = pooled["beta_used"].to_numpy(dtype="float64") if composed else None
    market_fwd = pooled["market_fwd"].to_numpy(dtype="float64") if composed else None

    model_loss, base_loss, blend_loss, weights = [], [], [], []
    carried = 0.0
    for train, test in folds:
        test_scale = scale[test]
        truth = total[test] * test_scale
        model_pred, base_pred = {}, {}
        alpha = {}
        for q in QUANTILES:
            learner = HistGradientBoostingRegressor(loss="quantile", quantile=q, **TREE)
            learner.fit(X[train], y[train])
            alpha[q] = learner.predict(X[test])
            model_pred[q] = alpha[q] * test_scale
            base_pred[q] = np.full(int(test.sum()),
                                   float(np.quantile(total[train], q))) * test_scale

        if composed:
            # 시장 밴드는 학습 구간의 무조건부 분위수로 잡는다 — 시장의 방향은
            # 예측하지 않는다. 폭만 쓴다.
            market_q = {q: float(np.quantile(market_fwd[train], q)) for q in QUANTILES}
            beta = beta_used[test]
            centre = alpha[0.5] + beta * market_q[0.5]
            for q in QUANTILES:
                if q == 0.5:
                    model_pred[q] = centre * test_scale
                    continue
                a = alpha[q] - alpha[0.5]
                m = beta * (market_q[q] - market_q[0.5])
                model_pred[q] = (centre + np.sign(q - 0.5) * np.sqrt(a * a + m * m)) * test_scale

        model_loss.append(np.mean([ml.pinball(truth, model_pred[q], q) for q in QUANTILES]))
        base_loss.append(np.mean([ml.pinball(truth, base_pred[q], q) for q in QUANTILES]))
        blend_loss.append(np.mean([
            ml.pinball(truth, carried * model_pred[q] + (1 - carried) * base_pred[q], q)
            for q in QUANTILES
        ]))
        weights.append(carried)

        grid = np.linspace(0.0, 1.0, 21)
        losses = [np.mean([ml.pinball(truth, w * model_pred[q] + (1 - w) * base_pred[q], q)
                           for q in QUANTILES]) for w in grid]
        carried = float(grid[int(np.argmin(losses))])

    base = float(np.mean(base_loss))
    return {
        "skill": 1.0 - float(np.mean(model_loss)) / base,
        "blend": 1.0 - float(np.mean(blend_loss)) / base,
        "weight": float(np.mean(weights)),
        # **정규화 자를 바꿔 가며 비교할 때는 이 둘만 본다.** 위의 비율은 기준선이
        # 같이 움직여서 공평하지 않다.
        "loss": float(np.mean(blend_loss)),
        "baseLoss": base,
        "rows": len(pooled),
    }


# 재 볼 조합. 지금까지의 최선(풀링 + 작은 트리 + 관심도)에 하나씩 얹는다.
VARIANTS = [
    ("기준(관심도까지)",      dict(use_market=False, hourly_scale=False, residual=False)),
    ("+ 시장요인",            dict(use_market=True,  hourly_scale=False, residual=False)),
    ("+ 시장요인 + 시각보정", dict(use_market=True,  hourly_scale=True,  residual=False)),
    ("잔차목표(진단용)",      dict(use_market=True,  hourly_scale=False, residual=True)),
    # 잔차로 학습하고 **총수익률로 채점**한다. 이게 실제로 쓸 수 있는 성적이다.
    ("잔차→합성(실전)",       dict(use_market=True,  hourly_scale=False, residual=True,
                                composed=True)),
    # 봉 범위로 잰 변동성 8축. 지금 모델이 아는 변동성은 종가끼리의 표준편차와 ATR
    # 뿐이라, 봉 안에서 벌어진 일을 거의 안 본다. 밴드는 이 저장소가 실제로 맞히는
    # 쪽이라(82.2%) 여기부터 잰다.
    ("+ 범위변동성",          dict(use_market=True,  hourly_scale=False, residual=False,
                                use_range=True)),
]


async def run_block(label: str, specs: list[Spec], timeframe: str, bars: int,
                    horizons: list[int], step_ms: int) -> None:
    print(f"\n{'=' * 82}\n{label} · {len(specs)}종목 × {bars}봉\n{'=' * 82}")
    started = time.time()
    items = [x for x in [await load(s, timeframe, bars) for s in specs] if x is not None]
    if not items:
        print("  쓸 수 있는 종목이 없다")
        return
    print(f"  적재 {len(items)}종목 ({time.time() - started:.0f}s)")

    for horizon in horizons:
        print(f"\n  --- 지평 {horizon}봉 ---")
        for name, options in VARIANTS:
            t0 = time.time()
            options = dict(options)
            composed = options.pop("composed", False)
            pooled, columns = assemble(items, horizon, **options)
            if pooled.empty or len(pooled) < 1000:
                print(f"    {name:24s} 표본 부족 ({len(pooled)})")
                continue
            result = evaluate(pooled, columns, horizon * step_ms, composed=composed)
            mark = "✓" if result["blend"] > 0.002 else " "
            print(f"  {mark} {name:24s} 단독 {result['skill']:+.4f} | "
                  f"섞으면 {result['blend']:+.4f} (비중 {result['weight']:.2f}) | "
                  f"{result['rows']:7,d}행 {time.time() - t0:5.0f}s")


async def scale_block(label: str, specs: list[Spec], timeframe: str, bars: int,
                      horizons: list[int], step_ms: int) -> None:
    """목표값을 무엇으로 나눌 것인가.

    ATR 은 1차 적률이고 가격 단위이며 연율화가 정의돼 있지 않다. 범위 추정량은
    2차 적률이라 분산과 바로 이어진다. 바꿀 이유는 구조적으로 있는데, 실제로
    나아지는지는 재 봐야 안다.

    **`skill` 을 쓰지 않는다.** 기준선이 자와 같이 움직여서 분모가 변한다.
    `truth` 는 자와 무관하게 원래 수익률이므로 절대 손실만 공평하다.
    """
    bar = "=" * 82
    print(f"\n{bar}\n{label} · 정규화 자 비교 · {len(specs)}종목 × {bars}봉\n{bar}")
    items = [x for x in [await load(s, timeframe, bars) for s in specs] if x is not None]
    if not items:
        print("  쓸 수 있는 종목이 없다")
        return
    print(f"  적재 {len(items)}종목")

    for horizon in horizons:
        print(f"\n  --- 지평 {horizon}봉 --- (손실은 작을수록 좋다)")
        first = None
        for kind in ("atr", "gk", "parkinson"):
            pooled, columns = assemble(items, horizon, use_market=True,
                                       hourly_scale=False, residual=False,
                                       scale_kind=kind)
            if pooled.empty or len(pooled) < 1000:
                print(f"    {kind:10s} 표본 부족 ({len(pooled)})")
                continue
            got = evaluate(pooled, columns, horizon * step_ms)
            if first is None:
                first = got["loss"]
            delta = (first - got["loss"]) / first * 100.0 if first else 0.0
            print(f"    {kind:10s} 손실 {got['loss']:.6f} · 기준선 {got['baseLoss']:.6f}"
                  f" · ATR 대비 {delta:+.2f}% · {got['rows']:,d}행")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    # 일봉만. 지금 실제로 되는 구간이라 새 축은 여기서 먼저 판가름 난다.
    parser.add_argument("--daily", action="store_true")
    # 정규화 자 비교(ATR / Garman-Klass / Parkinson). 절대 손실로만 잰다.
    parser.add_argument("--scale", action="store_true")
    args = parser.parse_args()

    if args.quick:
        await run_block("암호화폐 1시간봉", CRYPTO[:4], "1h", 4000, [6, 24], 3_600_000)
        return

    if args.scale:
        await scale_block("일봉 (암호화폐)", CRYPTO, "1d", 3000, [1, 3, 5, 10], 86_400_000)
        await scale_block("일봉 (미국주식)", STOCKS, "1d", 3000, [1, 3, 5, 10], 86_400_000)
        return

    if args.daily:
        await run_block("일봉 (암호화폐)", CRYPTO, "1d", 3000, [1, 3, 5, 10, 20], 86_400_000)
        await run_block("일봉 (미국주식)", STOCKS, "1d", 3000, [1, 3, 5, 10, 20], 86_400_000)
        return

    # 시간봉: 짧은 지평까지 내려간다. 지금껏 24봉 이상만 재봤다.
    await run_block("암호화폐 1시간봉", CRYPTO, "1h", 12000, [3, 6, 12, 24, 72], 3_600_000)
    # 15분봉: 더 세세한 쪽도 실제로 되는지.
    await run_block("암호화폐 15분봉", CRYPTO[:8], "15m", 12000, [4, 8, 24, 96], 900_000)
    # 일봉: 지금 유일하게 되는 구간. 새 축이 더 밀어 올리는지 본다.
    await run_block("일봉 (암호화폐)", CRYPTO, "1d", 3000, [5, 10, 20], 86_400_000)
    await run_block("일봉 (미국주식)", STOCKS, "1d", 3000, [5, 10, 20], 86_400_000)


if __name__ == "__main__":
    asyncio.run(main())
