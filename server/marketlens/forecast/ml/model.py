"""학습 예측층.

검색 기반은 "비슷한 과거가 이랬다"까지다. 여기서는 **그 요소들을 입력으로 넣고
실제로 무슨 일이 났는지를 학습**한다 — 모델이 "지금은 사례를 믿을 자리인가",
"이 상황에서는 폭이 넓어지는가"까지 배운다.

`scripts/sweep.py` 로 기법들을 재고 이긴 것만 여기 옮겼다. 재 본 결과:

- **목표값도 무차원이어야 한다.** 절대 로그수익률로 학습하면 변동성 수준이 바뀌는
  순간 트리가 학습 범위 밖으로 못 나가 무너진다(skill −0.35). 현재 ATR 로 나눈다.
- **강한 정규화가 크게 이긴다.** 느슨한 트리 −0.35 → 작은 트리 −0.06.
- **여러 종목을 모아 학습한다.** 한 종목 4천 행으로는 표본이 모자란다.
- **관심도 축(위키백과 조회수)이 도움이 된다.** 1시간봉에서 −0.060 → −0.019.
- **기준선과 섞는다.** 이게 결정적이다 — 모델 단독으로는 지는 구간에서도, 앞 폴드에서
  고른 비중으로 섞으면 기준선을 넘는다. 신호가 없으면 비중이 0으로 가 손해가 없다.
- **지평이 길수록 배운다.** 1시간봉 24봉은 안 되고, 일봉 10봉은 된다.

기준선이 둘인 이유:
- **단순 기준선** — 아무것도 모를 때의 무조건부 분위수.
- **변동성 기준선** — "앞으로의 폭 = k × 현재 ATR". 변동성 뭉침만으로 여기까지 간다.

**학습이 의미가 있으려면 변동성 기준선을 넘어야 한다.** 못 넘으면 `predict` 가 그
기준선을 그대로 쓰고 화면이 그렇게 말한다. 종목·봉마다 다르므로 학습할 때마다 다시 잰다.

정직하게 재는 장치:
- **시간 기준 퍼징 폴드** — 여러 종목을 섞으면 행 번호로 접을 수 없다. 같은 시각의
  다른 종목이 학습과 검증에 나뉘어 들어간다.
- **섞는 비중은 앞 폴드에서 고른다.** 검증 구간에서 고르면 자기 답을 보고 만든 성적이다.
- **컨포멀 보정** — 검증 구간의 실제 오차로 밴드를 넓힌다.
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ...core.candle import closed_only
from ...core.timeframe import to_ms
from ...events.schema import Event
from ...indicators import _math as indicator_math
from ...research import registry as research  # noqa: F401
from . import dataset
from .labels import triple_barrier

MODEL_DIR = Path("store_data/models")
QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)
BAND_PAIRS = ((0.1, 0.9, 0.8), (0.25, 0.75, 0.5))
MIN_ROWS = 400
ATR_PERIOD = 14
# 섞은 결과가 변동성 기준선을 이 이상 넘어야 실제로 쓴다. 0 근처의 승리는 잡음이다.
SKILL_THRESHOLD = 0.002
# 섞는 비중의 격자. 촘촘하게 잡을 이유가 없다 — 어차피 폴드 하나로 고르는 값이다.
BLEND_GRID = np.linspace(0.0, 1.0, 21)

# 스윕에서 이긴 설정. 느슨하게 풀면 바로 과적합한다.
TREE = dict(max_iter=80, learning_rate=0.03, max_depth=2,
            min_samples_leaf=250, l2_regularization=20.0, max_features=0.4,
            random_state=20260828)


class MissingDependency(RuntimeError):
    pass


def _regressor(quantile: float):
    try:
        from sklearn.ensemble import HistGradientBoostingRegressor
    except ImportError as exc:
        raise MissingDependency(
            "학습층은 scikit-learn 이 필요하다: pip install -e \".[ml]\""
        ) from exc
    return HistGradientBoostingRegressor(loss="quantile", quantile=quantile, **TREE)


def _classifier():
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
    except ImportError as exc:
        raise MissingDependency(
            "학습층은 scikit-learn 이 필요하다: pip install -e \".[ml]\""
        ) from exc
    return HistGradientBoostingClassifier(**TREE)


def volatility_scale(closed: pd.DataFrame) -> pd.Series:
    """목표값을 나눌 변동성 자. 0 이나 NaN 이면 그 행은 학습에서 빠진다."""
    scale = indicator_math.atr(closed, ATR_PERIOD) / closed["close"].astype("float64")
    return scale.replace(0.0, np.nan)


def horizon_steps(horizon: int, steps: int = 4) -> list[int]:
    """부채꼴을 그리려면 중간 지평도 필요하다. 최종 지평 하나만 배우면 곡선이 아니라 점이다."""
    return sorted({max(1, round(horizon * i / steps)) for i in range(1, steps + 1)})


def pinball(y: np.ndarray, pred: np.ndarray, quantile: float) -> float:
    """분위수 예측의 손실. 분위수를 맞히는지 재는 표준 척도다."""
    diff = y - pred
    return float(np.mean(np.maximum(quantile * diff, (quantile - 1.0) * diff)))


def time_folds(ts: np.ndarray, horizon_ms: int, count: int = 4) -> list[tuple]:
    """**시각** 기준으로 접는다.

    여러 종목을 모으면 행 번호로 접을 수 없다 — 같은 시각의 다른 종목이 학습과 검증에
    나뉘어 들어가고, 그 순간 검증이 학습을 훔쳐본다.
    """
    if len(ts) == 0:
        return []
    edges = np.linspace(float(ts.min()), float(ts.max()), count + 2)
    folds = []
    for i in range(count):
        train_end = edges[i + 1]
        test_start = train_end + horizon_ms      # 라벨이 겹치는 구간을 버린다
        train = ts <= train_end
        test = (ts > test_start) & (ts <= edges[i + 2])
        if train.sum() >= 200 and test.sum() >= 60:
            folds.append((train, test))
    return folds


@dataclass
class SymbolData:
    """학습에 넣을 한 종목. 첫 번째가 이 모델의 주인이다."""

    symbol: str
    df: pd.DataFrame
    events: list[Event] = field(default_factory=list)
    attention: pd.DataFrame | None = None


@dataclass
class Report:
    rows: int
    symbols: list[str]
    horizon: int
    window: int
    horizons: list[int]
    # 모델 단독이 변동성 기준선 대비.
    skill: dict[str, float] = field(default_factory=dict)
    # 기준선과 섞은 결과가 변동성 기준선 대비. 실제로 쓰는 건 이쪽이다.
    blend_skill: dict[str, float] = field(default_factory=dict)
    # 변동성 기준선이 단순 기준선 대비.
    vol_skill: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    coverage: dict[str, float] = field(default_factory=dict)
    direction_accuracy: float | None = None
    direction_baseline: float | None = None
    margins: dict[str, float] = field(default_factory=dict)
    importance: list[dict] = field(default_factory=list)
    folds: int = 0

    @property
    def learned_something(self) -> bool:
        final = self.blend_skill.get(str(self.horizon))
        return final is not None and final > SKILL_THRESHOLD

    def to_dict(self) -> dict:
        weight = self.weights.get(str(self.horizon), 0.0)
        return {
            "rows": self.rows,
            "symbols": self.symbols,
            "horizon": self.horizon,
            "window": self.window,
            "horizons": self.horizons,
            "folds": self.folds,
            "skill": {k: round(v, 4) for k, v in self.skill.items()},
            "blendSkill": {k: round(v, 4) for k, v in self.blend_skill.items()},
            "volSkill": {k: round(v, 4) for k, v in self.vol_skill.items()},
            "weights": {k: round(v, 3) for k, v in self.weights.items()},
            "coverage": {k: round(v, 4) for k, v in self.coverage.items()},
            "directionAccuracy": None if self.direction_accuracy is None
            else round(self.direction_accuracy, 4),
            "directionBaseline": None if self.direction_baseline is None
            else round(self.direction_baseline, 4),
            "margins": {k: round(v, 6) for k, v in self.margins.items()},
            "importance": self.importance,
            "learnedSomething": self.learned_something,
            "verdict": (
                f"학습이 변동성 기준선을 넘었다 — 예측은 모델 {weight:.0%} + 기준선 "
                f"{1 - weight:.0%} 로 섞어 쓴다."
                if self.learned_something else
                "학습이 변동성 기준선을 못 넘었다 — 밴드는 그 기준선으로 그린다. "
                "이 종목·봉에서는 지표·사례·관심도가 '현재 변동성으로 폭을 잡는 것' 이상을 주지 못했다."
            ),
            "citations": research.cite("purged_walk_forward", "conformal_intervals",
                                       "analog_retrieval_forecast", "volatility_clustering",
                                       "triple_barrier_labeling"),
        }


def assemble(datasets: list[SymbolData], window: int, horizon: int,
             steps: list[int]) -> tuple[pd.DataFrame, list[str]]:
    """여러 종목을 한 표로. 시각과 종목을 남겨 시간 기준으로 접을 수 있게 한다."""
    columns = list(dataset.FEATURE_COLUMNS)
    frames = []
    for item in datasets:
        closed = closed_only(item.df).reset_index(drop=True)
        panel = dataset.build(item.df, item.events, window=window, horizon=horizon,
                              attention_frame=item.attention)
        if panel.empty or len(panel) != len(closed):
            continue
        scale = volatility_scale(closed).to_numpy()
        frame = panel[columns].copy()
        frame["ts"] = panel["ts"]
        frame["scale"] = scale
        for h in steps:
            frame[f"y{h}"] = dataset.forward_return(closed, h).to_numpy() / scale
        frame["label"] = triple_barrier(closed, horizon=horizon)["label"].to_numpy()
        frame["symbol"] = item.symbol
        frames.append(frame)

    if not frames:
        raise ValueError("피처를 만들 만큼 봉이 있는 종목이 없다")
    pooled = pd.concat(frames, ignore_index=True)
    pooled = pooled.replace([np.inf, -np.inf], np.nan).sort_values("ts").reset_index(drop=True)
    return pooled, columns


def train(
    datasets: list[SymbolData],
    name: str,
    horizon: int = 24,
    window: int = 48,
    folds: int = 4,
    timeframe: str = "1h",
) -> dict:
    """학습하고, 두 기준선과 견주고, 섞을 비중을 정하고, 저장한다."""
    steps = horizon_steps(horizon)
    raw, columns = assemble(datasets, window, horizon, steps)
    frame = raw.drop(columns=["label"]).dropna().reset_index(drop=True)
    if len(frame) < MIN_ROWS:
        raise ValueError(f"학습 표가 {len(frame)}행뿐이다 — 최소 {MIN_ROWS}행은 필요하다")

    X = frame[columns].to_numpy(dtype="float64")
    scale = frame["scale"].to_numpy(dtype="float64")
    ts = frame["ts"].to_numpy()
    horizon_ms = horizon * to_ms(timeframe)
    fold_list = time_folds(ts, horizon_ms, folds)
    if not fold_list:
        raise ValueError("검증할 구간이 남지 않았다 — 기간이 더 길어야 한다")

    report = Report(rows=len(frame), symbols=[d.symbol for d in datasets],
                    horizon=horizon, window=window, horizons=steps, folds=len(fold_list))
    models: dict[tuple[int, float], object] = {}
    baseline_ratio: dict[tuple[int, float], float] = {}
    margins: dict[str, float] = {}
    weights: dict[int, float] = {}

    for h in steps:
        y = frame[f"y{h}"].to_numpy(dtype="float64")
        model_loss, vol_loss, naive_loss, blend_loss = [], [], [], []
        oof_pred = {q: [] for q in QUANTILES}
        oof_y: list[np.ndarray] = []
        # 앞 폴드에서 고른 비중을 다음 폴드에 쓴다. 첫 폴드는 기준선(0)으로 시작한다.
        carried = 0.0
        carried_history: list[float] = []

        for train_mask, test_mask in fold_list:
            test_scale = scale[test_mask]
            truth = y[test_mask] * test_scale
            oof_y.append(y[test_mask])

            model_pred, base_pred = {}, {}
            for q in QUANTILES:
                learner = _regressor(q)
                learner.fit(X[train_mask], y[train_mask])
                model_pred[q] = learner.predict(X[test_mask])
                base_pred[q] = np.full(test_mask.sum(), float(np.quantile(y[train_mask], q)))
                oof_pred[q].append(carried * model_pred[q] + (1 - carried) * base_pred[q])

            model_loss.append(np.mean([pinball(truth, model_pred[q] * test_scale, q)
                                       for q in QUANTILES]))
            vol_loss.append(np.mean([pinball(truth, base_pred[q] * test_scale, q)
                                     for q in QUANTILES]))
            blend_loss.append(np.mean([
                pinball(truth, (carried * model_pred[q] + (1 - carried) * base_pred[q]) * test_scale, q)
                for q in QUANTILES
            ]))
            absolute = {q: float(np.quantile(y[train_mask] * scale[train_mask], q))
                        for q in QUANTILES}
            naive_loss.append(np.mean([pinball(truth, np.full_like(truth, absolute[q]), q)
                                       for q in QUANTILES]))
            carried_history.append(carried)

            # 이번 폴드에서 최적 비중을 골라 다음 폴드로 넘긴다.
            grid_loss = [
                np.mean([pinball(truth, (w * model_pred[q] + (1 - w) * base_pred[q]) * test_scale, q)
                         for q in QUANTILES])
                for w in BLEND_GRID
            ]
            carried = float(BLEND_GRID[int(np.argmin(grid_loss))])

        mean_vol = float(np.mean(vol_loss))
        mean_naive = float(np.mean(naive_loss))
        report.skill[str(h)] = 1.0 - float(np.mean(model_loss)) / mean_vol if mean_vol else 0.0
        report.blend_skill[str(h)] = 1.0 - float(np.mean(blend_loss)) / mean_vol if mean_vol else 0.0
        report.vol_skill[str(h)] = 1.0 - mean_vol / mean_naive if mean_naive else 0.0
        # 배포에 쓸 비중은 마지막으로 고른 값이다 — 제일 최근 구간에서 정해진 것.
        weights[h] = carried
        report.weights[str(h)] = carried

        actual = np.concatenate(oof_y)
        stacked = {q: np.concatenate(oof_pred[q]) for q in QUANTILES}
        for low_q, high_q, target in BAND_PAIRS:
            low, high = stacked[low_q], stacked[high_q]
            conformity = np.maximum(low - actual, actual - high)
            level = min(0.999, target * (1 + 1 / len(conformity)))
            margin = float(max(0.0, np.quantile(conformity, level)))
            margins[f"{h}:{low_q}"] = margin
            report.coverage[f"{h}:{int(target * 100)}"] = float(
                np.mean((actual >= low - margin) & (actual <= high + margin))
            )

        for q in QUANTILES:
            learner = _regressor(q)
            learner.fit(X, y)
            models[(h, q)] = learner
            baseline_ratio[(h, q)] = float(np.quantile(y, q))

    report.margins = margins

    # --- 방향 분류 (삼중 장벽) ---
    classifier = None
    dir_frame = raw[columns + ["ts", "label"]].dropna().reset_index(drop=True)
    if len(dir_frame) >= MIN_ROWS:
        dx = dir_frame[columns].to_numpy(dtype="float64")
        dy = dir_frame["label"].to_numpy(dtype="int64")
        hits = base_hits = total = 0
        for train_mask, test_mask in time_folds(dir_frame["ts"].to_numpy(), horizon_ms, folds):
            model = _classifier()
            model.fit(dx[train_mask], dy[train_mask])
            truth = dy[test_mask]
            hits += int((model.predict(dx[test_mask]) == truth).sum())
            common = np.bincount(dy[train_mask] + 1).argmax() - 1
            base_hits += int((truth == common).sum())
            total += len(truth)
        if total:
            report.direction_accuracy = hits / total
            report.direction_baseline = base_hits / total
        classifier = _classifier()
        classifier.fit(dx, dy)

    report.importance = _importance(models[(steps[-1], 0.5)], X,
                                    frame[f"y{steps[-1]}"].to_numpy(), columns)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with (MODEL_DIR / f"{name}.pkl").open("wb") as handle:
        pickle.dump({
            "models": models, "classifier": classifier, "columns": columns,
            "horizon": horizon, "window": window, "horizons": steps,
            "margins": margins, "quantiles": QUANTILES,
            "baselineRatio": baseline_ratio, "weights": weights,
            "useModel": report.learned_something,
            "symbols": report.symbols, "timeframe": timeframe,
        }, handle)
    payload = report.to_dict()
    (MODEL_DIR / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def _importance(model, X: np.ndarray, y: np.ndarray, columns: list[str],
                top: int = 12) -> list[dict]:
    """무엇을 보고 판단하는지. 유사구간·이벤트·관심도 축이 실제로 쓰이는지 드러난다."""
    try:
        from sklearn.inspection import permutation_importance
    except ImportError:
        return []
    sample = slice(max(0, len(X) - 1200), len(X))
    try:
        result = permutation_importance(
            model, X[sample], y[sample], n_repeats=3, random_state=20260828, n_jobs=1
        )
    except Exception:  # noqa: BLE001 - 중요도는 있으면 좋은 것이지 없으면 못 도는 게 아니다
        return []
    order = np.argsort(-result.importances_mean)[:top]
    return [
        {"feature": columns[i], "score": round(float(result.importances_mean[i]), 6)}
        for i in order
        if result.importances_mean[i] > 0
    ]


def load(name: str) -> dict | None:
    path = MODEL_DIR / f"{name}.pkl"
    if not path.is_file():
        return None
    with path.open("rb") as handle:
        return pickle.load(handle)


def report(name: str) -> dict | None:
    path = MODEL_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def available() -> list[dict]:
    if not MODEL_DIR.is_dir():
        return []
    out = []
    for path in sorted(MODEL_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        horizon = str(payload.get("horizon"))
        out.append({
            "name": path.stem,
            "horizon": payload.get("horizon"),
            "rows": payload.get("rows"),
            "symbols": payload.get("symbols", []),
            "learnedSomething": payload.get("learnedSomething"),
            "skill": payload.get("blendSkill", {}).get(horizon),
        })
    return out


def predict(
    df: pd.DataFrame,
    name: str,
    events: list[Event] | None = None,
    timeframe: str = "1h",
    attention_frame: pd.DataFrame | None = None,
) -> dict:
    """마지막 봉 기준으로 앞으로의 분위수 곡선을 낸다.

    학습이 기준선을 넘었으면 **모델과 기준선을 섞어** 쓰고, 못 넘었으면 기준선만 쓴다.
    무엇을 얼마나 썼는지 `source` 와 `weight` 로 알린다.
    """
    bundle = load(name)
    if bundle is None:
        return {"available": False, "reason": f"학습된 모델이 없다: {name}"}

    panel = dataset.build(df, events, window=bundle["window"], horizon=bundle["horizon"],
                          attention_frame=attention_frame)
    if panel.empty:
        return {"available": False, "reason": "피처를 만들 만큼 봉이 없다"}
    row = panel[bundle["columns"]].dropna().tail(1)
    if row.empty:
        return {"available": False, "reason": "마지막 봉의 피처가 아직 덜 데워졌다"}

    closed = closed_only(df).reset_index(drop=True)
    scale = float(volatility_scale(closed).iloc[-1])
    if not np.isfinite(scale) or scale <= 0:
        return {"available": False, "reason": "변동성을 잴 수 없다 (ATR 이 0이거나 비었다)"}

    last_close = float(closed["close"].iloc[-1])
    last_ts = int(closed["ts"].iloc[-1])
    step = to_ms(timeframe) // 1000
    x = row.to_numpy(dtype="float64")
    use_model = bool(bundle.get("useModel", False))
    steps = bundle["horizons"]

    raw: dict[float, list[float]] = {}
    # 화면에 보여줄 비중은 **최종 지평**의 것이다. 지평마다 다른데 최댓값을 쓰면
    # 리포트에 85% 라 적어 놓고 화면에는 100% 라고 나온다.
    used_weight = (float(bundle.get("weights", {}).get(steps[-1], 0.0))
                   if use_model else 0.0)
    for q in bundle["quantiles"]:
        values = []
        for h in steps:
            weight = float(bundle.get("weights", {}).get(h, 0.0)) if use_model else 0.0
            base = float(bundle["baselineRatio"][(h, q)])
            ratio = base
            if weight > 0:
                ratio = weight * float(bundle["models"][(h, q)].predict(x)[0]) + (1 - weight) * base
            margin = 0.0
            for low_q, high_q, _ in BAND_PAIRS:
                if q == low_q:
                    margin = -bundle["margins"].get(f"{h}:{low_q}", 0.0)
                elif q == high_q:
                    margin = bundle["margins"].get(f"{h}:{low_q}", 0.0)
            values.append((ratio + margin) * scale)
        raw[q] = values

    horizon = bundle["horizon"]
    grid = np.arange(0, horizon + 1)
    bands: dict[str, list[dict]] = {}
    for q, values in raw.items():
        curve = np.interp(grid, [0] + steps, [0.0] + values)
        bands[f"p{int(q * 100)}"] = [
            {"time": int(last_ts // 1000 + step * int(h)),
             "value": last_close * float(np.exp(curve[int(h)]))}
            for h in grid
        ]

    median_final = raw[0.5][-1]
    saved = report(name) or {}
    blended = use_model and used_weight > 0
    result = {
        "available": True,
        "model": name,
        "source": "blend" if blended else "volatility-baseline",
        "sourceLabel": (f"모델 {used_weight:.0%} + 기준선 {1 - used_weight:.0%}"
                        if blended else "변동성 기준선"),
        "weight": round(used_weight, 3) if blended else 0.0,
        "horizon": horizon,
        "last": last_close,
        "lastTs": last_ts,
        "atrPct": round(scale * 100, 4),
        "bands": bands,
        "median": last_close * float(np.exp(median_final)),
        "expectedMovePct": float(np.expm1(median_final) * 100.0),
        "probUp": _prob_up({q: values[-1] for q, values in raw.items()}),
        "report": saved,
        "verdict": saved.get("verdict"),
    }
    if bundle.get("classifier") is not None:
        proba = bundle["classifier"].predict_proba(x)[0]
        classes = [int(c) for c in bundle["classifier"].classes_]
        ranked = dict(zip(classes, (float(p) for p in proba)))
        best = max(ranked, key=ranked.get)
        beat = (saved.get("directionAccuracy") or 0) > (saved.get("directionBaseline") or 1)
        result["direction"] = best
        result["directionConfidence"] = round(ranked[best], 4)
        result["directionBeatsBaseline"] = bool(beat)
        result["probabilities"] = {str(k): round(v, 4) for k, v in ranked.items()}
    return result


def _prob_up(final: dict[float, float]) -> float:
    """분위수 곡선에서 0(무변화)이 몇 번째 분위에 오는지 보간해 상승 확률을 읽는다."""
    quantiles = sorted(final)
    values = [final[q] for q in quantiles]
    if values[0] > 0:
        return 1.0
    if values[-1] < 0:
        return 0.0
    below = float(np.interp(0.0, values, quantiles))
    return round(1.0 - below, 4)
