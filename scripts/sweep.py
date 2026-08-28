"""학습 기법 실험 장치.

"이 기법이 실제로 도움이 되는가" 를 재는 곳이다. 모델 코드에 기법을 먼저 넣고
나중에 재면, 안 되는 기법이 그대로 남는다 — 여기서 재고 이긴 것만 옮긴다.

돌리는 법:
    .venv/Scripts/python scripts/sweep.py            # 기본 스윕
    .venv/Scripts/python scripts/sweep.py --quick    # 짧게

모든 비교는 **같은 검증 구간, 같은 손실**로 한다. 기준선을 바꿔 가며 이기는 것은
이기는 게 아니다.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
warnings.filterwarnings("ignore")

from marketlens import events as event_layer  # noqa: E402
from marketlens.core.candle import closed_only  # noqa: E402
from marketlens.events.sources import attention  # noqa: E402
from marketlens.forecast.ml import dataset  # noqa: E402
from marketlens.forecast.ml import model as ml  # noqa: E402
from marketlens.providers import get as get_provider  # noqa: E402

QUANTILES = (0.1, 0.5, 0.9)
FOLDS = 4


# --------------------------------------------------------------------------- 데이터

@dataclass
class Series:
    symbol: str
    provider: str
    timeframe: str
    bars: int
    market: str = "crypto"


CRYPTO_1H = [
    Series("BTCUSDT", "binance", "1h", 5000),
    Series("ETHUSDT", "binance", "1h", 5000),
    Series("SOLUSDT", "binance", "1h", 5000),
    Series("XRPUSDT", "binance", "1h", 5000),
    Series("BNBUSDT", "binance", "1h", 5000),
    Series("DOGEUSDT", "binance", "1h", 5000),
]
DAILY = [
    Series("BTCUSDT", "binance", "1d", 3000),
    Series("ETHUSDT", "binance", "1d", 3000),
    Series("AAPL", "yahoo", "1d", 3000, "us"),
    Series("MSFT", "yahoo", "1d", 3000, "us"),
    Series("NVDA", "yahoo", "1d", 3000, "us"),
    Series("^GSPC", "yahoo", "1d", 3000, "us"),
]


@dataclass
class Panel:
    """한 종목의 피처 표 + 가격. 라벨은 나중에 지평별로 붙인다."""

    symbol: str
    features: pd.DataFrame
    closed: pd.DataFrame
    attention: pd.DataFrame


async def load(series: Series, window: int, horizon: int, use_attention: bool) -> Panel | None:
    try:
        df = await get_provider(series.provider).history(series.symbol, series.timeframe,
                                                         series.bars)
    except Exception as exc:  # noqa: BLE001
        print(f"    {series.symbol}: 시세 실패 — {exc}")
        return None
    found, _ = await event_layer.collect(df, series.symbol, series.market)
    relevant = event_layer.relevant(found, series.symbol, series.market)

    features = dataset.build(df, relevant, window=window, horizon=horizon)
    if features.empty:
        return None
    closed = closed_only(df).reset_index(drop=True)

    attention_frame = pd.DataFrame(index=features.index)
    if use_attention:
        attention_frame, _ = await attention.collect(closed, series.symbol)
    return Panel(series.symbol, features, closed, attention_frame)


# --------------------------------------------------------------------------- 표 만들기

def assemble(panels: list[Panel], horizon: int, columns: list[str],
             use_attention: bool) -> pd.DataFrame:
    """여러 종목을 한 표로. 시각을 남겨 시간 기준으로 접을 수 있게 한다."""
    frames = []
    for panel in panels:
        frame = panel.features[columns].copy()
        if use_attention:
            for column in attention.COLUMNS:
                frame[column] = panel.attention.get(column, np.nan)
        scale = ml.volatility_scale(panel.closed).to_numpy()
        frame["ts"] = panel.features["ts"]
        frame["scale"] = scale
        frame["y"] = dataset.forward_return(panel.closed, horizon).to_numpy() / scale
        frame["symbol"] = panel.symbol
        frames.append(frame)
    pooled = pd.concat(frames, ignore_index=True)
    return pooled.replace([np.inf, -np.inf], np.nan).dropna().sort_values("ts").reset_index(drop=True)


def time_folds(ts: np.ndarray, horizon_ms: int, count: int = FOLDS) -> list[tuple]:
    """시각 기준으로 접는다. 여러 종목을 섞으면 행 번호로 접을 수 없다 —
    같은 시각의 다른 종목이 학습과 검증에 나뉘어 들어간다."""
    lo, hi = ts.min(), ts.max()
    edges = np.linspace(lo, hi, count + 2)
    folds = []
    for i in range(count):
        train_end = edges[i + 1]
        test_start = train_end + horizon_ms       # 라벨이 겹치는 구간을 버린다
        test_end = edges[i + 2]
        train = ts <= train_end
        test = (ts > test_start) & (ts <= test_end)
        if train.sum() >= 300 and test.sum() >= 100:
            folds.append((train, test))
    return folds


# --------------------------------------------------------------------------- 학습기

def make_learner(kind: str, quantile: float, params: dict):
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import QuantileRegressor

    if kind == "linear":
        # 선형 분위수 회귀. 트리보다 훨씬 덜 과적합하고, 외삽도 된다.
        return QuantileRegressor(quantile=quantile, alpha=params.get("alpha", 0.01),
                                 solver="highs")
    return HistGradientBoostingRegressor(loss="quantile", quantile=quantile,
                                         random_state=1, **params)


TREE_LIGHT = dict(max_iter=120, learning_rate=0.04, max_depth=3,
                  min_samples_leaf=120, l2_regularization=5.0, max_features=0.6)
TREE_TINY = dict(max_iter=80, learning_rate=0.03, max_depth=2,
                 min_samples_leaf=250, l2_regularization=20.0, max_features=0.4)


def recency_weights(ts: np.ndarray, half_life_days: float) -> np.ndarray:
    """오래된 표본을 덜 본다. 시장은 정상적(stationary)이지 않다."""
    age_days = (ts.max() - ts) / 86_400_000.0
    return np.power(0.5, age_days / half_life_days)


@dataclass
class Result:
    name: str
    skill: float
    blend_skill: float
    weight: float
    rows: int
    seconds: float
    extra: dict = field(default_factory=dict)


def evaluate(pooled: pd.DataFrame, columns: list[str], horizon_ms: int,
             kind: str, params: dict, half_life: float | None,
             name: str) -> Result:
    """변동성 기준선 대비 skill 과, 기준선과 섞었을 때의 skill 을 같이 낸다.

    섞기(blend)를 재는 이유: 모델에 조금이라도 신호가 있으면 최적 가중 조합은
    기준선보다 나쁠 수 없다. 반대로 조합해도 안 나아지면 신호가 없는 것이다.
    """
    started = time.time()
    X = pooled[columns].to_numpy(dtype="float64")
    y = pooled["y"].to_numpy(dtype="float64")
    scale = pooled["scale"].to_numpy(dtype="float64")
    ts = pooled["ts"].to_numpy()
    folds = time_folds(ts, horizon_ms)
    if not folds:
        return Result(name, float("nan"), float("nan"), 0.0, len(pooled), 0.0)

    model_loss, base_loss, blend_loss = [], [], []
    best_weights = []
    # 실제로 쓸 수 있는 방식으로 섞는다: **앞 폴드에서 고른 비중**을 다음 폴드에 쓴다.
    # 검증 구간에서 비중을 고르면 그 성적은 자기 답을 보고 만든 것이라 못 믿는다.
    carried_weight = 0.0

    for train, test in folds:
        weights = recency_weights(ts[train], half_life) if half_life else None
        test_scale = scale[test]
        truth = y[test] * test_scale

        model_preds, base_preds = {}, {}
        for q in QUANTILES:
            learner = make_learner(kind, q, params)
            try:
                learner.fit(X[train], y[train], sample_weight=weights)
            except TypeError:
                learner.fit(X[train], y[train])
            model_preds[q] = learner.predict(X[test]) * test_scale
            base_preds[q] = float(np.quantile(y[train], q)) * test_scale

        model_loss.append(np.mean([ml.pinball(truth, model_preds[q], q) for q in QUANTILES]))
        base_loss.append(np.mean([ml.pinball(truth, base_preds[q], q) for q in QUANTILES]))

        # 앞 폴드에서 물려받은 비중으로 이번 폴드를 채점한다.
        blend_loss.append(np.mean([
            ml.pinball(truth,
                       carried_weight * model_preds[q] + (1 - carried_weight) * base_preds[q], q)
            for q in QUANTILES
        ]))
        best_weights.append(carried_weight)

        # 그리고 이번 폴드에서 최적 비중을 골라 다음 폴드로 넘긴다.
        grid = np.linspace(0.0, 1.0, 21)
        losses = [
            np.mean([ml.pinball(truth, w * model_preds[q] + (1 - w) * base_preds[q], q)
                     for q in QUANTILES])
            for w in grid
        ]
        carried_weight = float(grid[int(np.argmin(losses))])

    base = float(np.mean(base_loss))
    return Result(
        name=name,
        skill=1.0 - float(np.mean(model_loss)) / base,
        # 앞 폴드에서 고른 비중을 다음 폴드에 쓴 성적. 실제로 배포 가능한 절차다.
        blend_skill=1.0 - float(np.mean(blend_loss)) / base,
        weight=float(np.mean(best_weights)),
        rows=len(pooled),
        seconds=time.time() - started,
    )


# --------------------------------------------------------------------------- 스윕

async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()

    base_columns = list(dataset.FEATURE_COLUMNS)
    window = 48

    plans = [
        ("암호화폐 1시간봉", CRYPTO_1H[:2] if args.quick else CRYPTO_1H, "1h",
         [24] if args.quick else [24, 72, 168]),
        ("일봉 (암호화폐+주식)", DAILY[:2] if args.quick else DAILY, "1d",
         [10] if args.quick else [5, 10, 20, 60]),
    ]

    for label, series_list, timeframe, horizons in plans:
        print(f"\n{'=' * 78}\n{label} · {len(series_list)}종목\n{'=' * 78}")
        for horizon in horizons:
            step_ms = {"1h": 3_600_000, "1d": 86_400_000}[timeframe]
            horizon_ms = horizon * step_ms

            print(f"\n--- 지평 {horizon}봉 ---")
            panels = []
            for series in series_list:
                panel = await load(series, window, horizon, use_attention=True)
                if panel is not None:
                    panels.append(panel)
            if not panels:
                print("    쓸 수 있는 종목이 없다")
                continue

            single = assemble(panels[:1], horizon, base_columns, False)
            pooled = assemble(panels, horizon, base_columns, False)
            pooled_attn = assemble(panels, horizon, base_columns, True)
            attn_columns = base_columns + list(attention.COLUMNS)

            runs = [
                ("① 단일종목 · 트리", single, base_columns, "tree", TREE_LIGHT, None),
                ("② 여러종목 · 트리", pooled, base_columns, "tree", TREE_LIGHT, None),
                ("③ 여러종목 · 작은트리", pooled, base_columns, "tree", TREE_TINY, None),
                ("④ 여러종목 · 선형", pooled, base_columns, "linear", {"alpha": 0.01}, None),
                ("⑤ ④ + 최근가중(180일)", pooled, base_columns, "linear", {"alpha": 0.01}, 180.0),
                ("⑥ ④ + 관심도축", pooled_attn, attn_columns, "linear", {"alpha": 0.01}, None),
                ("⑦ ③ + 관심도축", pooled_attn, attn_columns, "tree", TREE_TINY, None),
            ]
            for name, frame, columns, kind, params, half_life in runs:
                if frame.empty or len(frame) < 500:
                    print(f"  {name:26s} 표본 부족")
                    continue
                result = evaluate(frame, columns, horizon_ms, kind, params, half_life, name)
                flag = "✓" if result.skill > 0 else " "
                print(f"  {flag} {name:26s} skill {result.skill:+.4f} | "
                      f"섞으면 {result.blend_skill:+.4f} (비중 {result.weight:.2f}) | "
                      f"{result.rows:6d}행 {result.seconds:5.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
