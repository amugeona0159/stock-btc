"""학습 예측층.

검색 기반은 "비슷한 과거가 이랬다"까지다. 여기서는 **그 요소들을 입력으로 넣고
실제로 무슨 일이 났는지를 학습**한다 — 모델이 "지금은 사례를 믿을 자리인가",
"이 상황에서는 폭이 넓어지는가"까지 배운다.

내는 것은 방향 하나가 아니라 **분위수 곡선**이다. 지평마다 10·25·50·75·90% 를 따로
학습해 앞으로의 부채꼴을 그린다. 방향만 찍으면 손절과 목표가를 못 만든다.

**목표값은 ATR 단위다.** 절대 로그수익률로 학습하면 변동성 수준이 바뀌는 순간 트리가
학습 범위 밖으로 못 나가 무너진다(실측: skill −0.35). 현재 ATR 로 나눠 무차원으로
만들면 레짐이 바뀌어도 같은 표를 쓴다. 피처를 무차원으로 만든 것과 같은 이유다.

기준선이 둘인 이유가 여기 있다:
- **단순 기준선** — 아무것도 모를 때의 무조건부 분위수.
- **변동성 기준선** — "앞으로의 폭 = k × 현재 ATR". 변동성 뭉침만으로 여기까지 간다.
  BTC 1시간봉에서 단순 기준선 대비 skill +0.22 를 낸다.

**학습이 의미가 있으려면 변동성 기준선을 넘어야 한다.** 못 넘으면 `predict` 가 그
기준선을 그대로 쓰고 화면이 그렇게 말한다 — 못 넘었는데 모델을 쓰면 더 나쁜 답을 낸다.
종목과 봉이 바뀌면 결과가 달라지므로, 판정은 학습할 때마다 다시 잰다.

정직하게 재는 장치:
- **퍼징 워크포워드** — 라벨이 미래 h봉을 보므로 학습·검증 경계에서 h봉을 버린다.
- **컨포멀 보정** — 검증 구간의 실제 오차로 밴드를 넓힌다. 학습 데이터에서 잰 폭은
  거의 항상 좁다(`research.library: conformal_intervals`).
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ...core.timeframe import to_ms
from ...events.schema import Event
from ...indicators import _math as indicator_math
from ...research import registry as research
from . import dataset
from .labels import triple_barrier

MODEL_DIR = Path("store_data/models")
QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)
# 밴드 짝과 목표 커버리지. 컨포멀 보정이 이 짝 단위로 폭을 정한다.
BAND_PAIRS = ((0.1, 0.9, 0.8), (0.25, 0.75, 0.5))
MIN_ROWS = 400
# 목표값을 무차원으로 만들 때 쓰는 변동성 자.
ATR_PERIOD = 14
# 학습 모델이 변동성 기준선을 이 이상 넘어야 실제로 쓴다. 0 근처의 승리는 잡음이다.
SKILL_THRESHOLD = 0.01


class MissingDependency(RuntimeError):
    pass


def _regressor(quantile: float):
    try:
        from sklearn.ensemble import HistGradientBoostingRegressor
    except ImportError as exc:
        raise MissingDependency(
            "학습층은 scikit-learn 이 필요하다: pip install -e \".[ml]\""
        ) from exc
    # 강하게 눌러 둔다. 4천 행에 39개 피처면 조금만 풀어도 바로 과적합한다 —
    # 느슨한 설정(max_iter 200 · depth 4)에서는 skill 이 −0.35 까지 떨어졌다.
    return HistGradientBoostingRegressor(
        loss="quantile", quantile=quantile,
        max_iter=120, learning_rate=0.04, max_depth=3,
        min_samples_leaf=120, l2_regularization=5.0, max_features=0.6,
        random_state=20260828,
    )


def _classifier():
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
    except ImportError as exc:
        raise MissingDependency(
            "학습층은 scikit-learn 이 필요하다: pip install -e \".[ml]\""
        ) from exc
    return HistGradientBoostingClassifier(
        max_iter=120, learning_rate=0.04, max_depth=3,
        min_samples_leaf=120, l2_regularization=5.0, max_features=0.6,
        random_state=20260828,
    )


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


@dataclass
class Fold:
    train_end: int
    test_start: int
    test_end: int


def purged_folds(size: int, horizon: int, count: int = 4) -> list[Fold]:
    """앞에서 뒤로 접어 가며, 경계에서 horizon 봉을 버린다."""
    step = size // (count + 1)
    folds: list[Fold] = []
    for i in range(count):
        train_end = step * (i + 1)
        test_start = train_end + horizon      # 라벨이 겹치는 구간을 버린다
        test_end = min(size, test_start + step)
        if test_end - test_start >= 40:
            folds.append(Fold(train_end, test_start, test_end))
    return folds


@dataclass
class Report:
    rows: int
    horizon: int
    window: int
    horizons: list[int]
    # 변동성 기준선 대비. 이게 0 이하면 학습이 아무것도 못 더한 것이다.
    skill: dict[str, float] = field(default_factory=dict)
    # 아무것도 모를 때 대비. 변동성 스케일링만으로 여기까지 간다.
    vol_skill: dict[str, float] = field(default_factory=dict)
    coverage: dict[str, float] = field(default_factory=dict)
    direction_accuracy: float | None = None
    direction_baseline: float | None = None
    margins: dict[str, float] = field(default_factory=dict)
    importance: list[dict] = field(default_factory=list)
    folds: int = 0

    @property
    def learned_something(self) -> bool:
        """변동성 기준선을 못 넘으면 배운 게 없는 것이다. `predict` 가 이 값을 보고 갈린다."""
        final = self.skill.get(str(self.horizon))
        return final is not None and final > SKILL_THRESHOLD

    def to_dict(self) -> dict:
        return {
            "rows": self.rows,
            "horizon": self.horizon,
            "window": self.window,
            "horizons": self.horizons,
            "folds": self.folds,
            "skill": {k: round(v, 4) for k, v in self.skill.items()},
            "volSkill": {k: round(v, 4) for k, v in self.vol_skill.items()},
            "coverage": {k: round(v, 4) for k, v in self.coverage.items()},
            "directionAccuracy": None if self.direction_accuracy is None
            else round(self.direction_accuracy, 4),
            "directionBaseline": None if self.direction_baseline is None
            else round(self.direction_baseline, 4),
            "margins": {k: round(v, 6) for k, v in self.margins.items()},
            "importance": self.importance,
            "learnedSomething": self.learned_something,
            "verdict": (
                "학습 모델이 변동성 기준선을 넘었다 — 모델 예측을 쓴다."
                if self.learned_something else
                "학습 모델이 변동성 기준선을 못 넘었다 — 밴드는 그 기준선으로 그린다. "
                "이 종목·봉에서는 지표와 사례가 '현재 변동성으로 폭을 잡는 것' 이상을 주지 못했다."
            ),
            "citations": research.cite("purged_walk_forward", "conformal_intervals",
                                       "analog_retrieval_forecast", "volatility_clustering",
                                       "triple_barrier_labeling"),
        }


def _training_frame(df: pd.DataFrame, events: list[Event] | None, window: int,
                    horizon: int, steps: list[int]) -> tuple[pd.DataFrame, list[str]]:
    """피처 + ATR 단위 목표값. 라벨은 캔들에서, 피처는 패널에서 온다."""
    closed = dataset.closed_only(df).reset_index(drop=True)
    panel = dataset.build(df, events, window=window, horizon=horizon)
    if panel.empty:
        raise ValueError("피처를 만들 만큼 봉이 없다")
    if len(panel) != len(closed):
        raise RuntimeError("피처 표와 캔들의 행 수가 다르다 — 정렬이 깨졌다")

    columns = list(dataset.FEATURE_COLUMNS)
    scale = volatility_scale(closed).to_numpy()
    frame = panel[columns].copy()
    frame["scale"] = scale
    for h in steps:
        # ATR 단위로 나눈 목표값. 예측은 다시 곱해 되돌린다.
        frame[f"y{h}"] = dataset.forward_return(closed, h).to_numpy() / scale
    frame["label"] = triple_barrier(closed, horizon=horizon)["label"].to_numpy()
    return frame.replace([np.inf, -np.inf], np.nan), columns


def train(
    df: pd.DataFrame,
    name: str,
    events: list[Event] | None = None,
    horizon: int = 24,
    window: int = 48,
    folds: int = 4,
) -> dict:
    """학습하고, 두 기준선과 견주고, 저장한다."""
    steps = horizon_steps(horizon)
    raw, columns = _training_frame(df, events, window, horizon, steps)
    frame = raw.drop(columns=["label"]).dropna()
    if len(frame) < MIN_ROWS:
        raise ValueError(f"학습 표가 {len(frame)}행뿐이다 — 최소 {MIN_ROWS}행은 필요하다")

    X = frame[columns].to_numpy(dtype="float64")
    scale = frame["scale"].to_numpy(dtype="float64")
    report = Report(rows=len(frame), horizon=horizon, window=window, horizons=steps)
    fold_list = purged_folds(len(frame), horizon, folds)
    report.folds = len(fold_list)
    if not fold_list:
        raise ValueError("검증할 구간이 남지 않았다 — 데이터가 더 필요하다")

    models: dict[tuple[int, float], object] = {}
    baseline_ratio: dict[tuple[int, float], float] = {}
    margins: dict[str, float] = {}

    for h in steps:
        y = frame[f"y{h}"].to_numpy(dtype="float64")
        oof = {q: [] for q in QUANTILES}
        oof_y: list[np.ndarray] = []
        model_loss = {q: [] for q in QUANTILES}
        vol_loss = {q: [] for q in QUANTILES}
        naive_loss = {q: [] for q in QUANTILES}

        for fold in fold_list:
            train_slice = slice(0, fold.train_end)
            test_slice = slice(fold.test_start, fold.test_end)
            test_scale = scale[test_slice]
            # 비교는 전부 **실제 수익률 단위**로 되돌려서 한다. 비율 단위로 재면
            # 변동성 기준선이 공짜로 이긴다.
            truth = y[test_slice] * test_scale
            oof_y.append(y[test_slice])

            for q in QUANTILES:
                model = _regressor(q)
                model.fit(X[train_slice], y[train_slice])
                pred = model.predict(X[test_slice])
                oof[q].append(pred)
                model_loss[q].append(pinball(truth, pred * test_scale, q))

                # 변동성 기준선: 학습 구간의 무조건부 '비율' 분위수 × 지금 ATR.
                ratio_q = float(np.quantile(y[train_slice], q))
                vol_loss[q].append(pinball(truth, ratio_q * test_scale, q))

                # 단순 기준선: 학습 구간의 무조건부 '절대' 분위수.
                absolute_q = float(np.quantile(y[train_slice] * scale[train_slice], q))
                naive_loss[q].append(pinball(truth, np.full_like(truth, absolute_q), q))

        mean_model = float(np.mean([np.mean(model_loss[q]) for q in QUANTILES]))
        mean_vol = float(np.mean([np.mean(vol_loss[q]) for q in QUANTILES]))
        mean_naive = float(np.mean([np.mean(naive_loss[q]) for q in QUANTILES]))
        report.skill[str(h)] = 1.0 - mean_model / mean_vol if mean_vol > 0 else 0.0
        report.vol_skill[str(h)] = 1.0 - mean_vol / mean_naive if mean_naive > 0 else 0.0

        actual = np.concatenate(oof_y)
        stacked = {q: np.concatenate(oof[q]) for q in QUANTILES}
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
            model = _regressor(q)
            model.fit(X, y)
            models[(h, q)] = model
            # 모델이 기준선을 못 넘었을 때 쓸 값. 전 구간의 비율 분위수다.
            baseline_ratio[(h, q)] = float(np.quantile(y, q))

    report.margins = margins

    # --- 방향 분류 (삼중 장벽) ---
    classifier = None
    dir_frame = raw[columns + ["label"]].dropna()
    if len(dir_frame) >= MIN_ROWS:
        dx = dir_frame[columns].to_numpy(dtype="float64")
        dy = dir_frame["label"].to_numpy(dtype="int64")
        hits = base_hits = total = 0
        for fold in purged_folds(len(dir_frame), horizon, folds):
            model = _classifier()
            model.fit(dx[: fold.train_end], dy[: fold.train_end])
            truth = dy[fold.test_start : fold.test_end]
            hits += int((model.predict(dx[fold.test_start : fold.test_end]) == truth).sum())
            # 기준선: 학습 구간에서 제일 흔한 라벨만 찍는 모델.
            common = np.bincount(dy[: fold.train_end] + 1).argmax() - 1
            base_hits += int((truth == common).sum())
            total += len(truth)
        if total:
            report.direction_accuracy = hits / total
            report.direction_baseline = base_hits / total
        classifier = _classifier()
        classifier.fit(dx, dy)

    report.importance = _importance(
        models[(steps[-1], 0.5)], X, frame[f"y{steps[-1]}"].to_numpy(), columns
    )

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with (MODEL_DIR / f"{name}.pkl").open("wb") as handle:
        pickle.dump({
            "models": models, "classifier": classifier, "columns": columns,
            "horizon": horizon, "window": window, "horizons": steps,
            "margins": margins, "quantiles": QUANTILES,
            "baselineRatio": baseline_ratio,
            "useModel": report.learned_something,
        }, handle)
    payload = report.to_dict()
    (MODEL_DIR / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def _importance(model, X: np.ndarray, y: np.ndarray, columns: list[str],
                top: int = 12) -> list[dict]:
    """무엇을 보고 판단하는지. 유사구간·이벤트 피처가 실제로 쓰이는지 여기서 드러난다."""
    try:
        from sklearn.inspection import permutation_importance
    except ImportError:
        return []
    sample = slice(max(0, len(X) - 800), len(X))
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
        out.append({
            "name": path.stem,
            "horizon": payload.get("horizon"),
            "rows": payload.get("rows"),
            "learnedSomething": payload.get("learnedSomething"),
            "skill": payload.get("skill", {}).get(str(payload.get("horizon"))),
        })
    return out


def predict(
    df: pd.DataFrame,
    name: str,
    events: list[Event] | None = None,
    timeframe: str = "1h",
) -> dict:
    """마지막 봉 기준으로 앞으로의 분위수 곡선을 낸다.

    학습 모델이 변동성 기준선을 못 넘었으면 그 기준선을 그대로 쓴다. 진 모델을
    쓰는 것보다 낫고, 무엇을 썼는지 `source` 로 알린다.
    """
    bundle = load(name)
    if bundle is None:
        return {"available": False, "reason": f"학습된 모델이 없다: {name}"}

    panel = dataset.build(df, events, window=bundle["window"], horizon=bundle["horizon"])
    if panel.empty:
        return {"available": False, "reason": "피처를 만들 만큼 봉이 없다"}
    row = panel[bundle["columns"]].dropna().tail(1)
    if row.empty:
        return {"available": False, "reason": "마지막 봉의 피처가 아직 덜 데워졌다"}

    closed = dataset.closed_only(df).reset_index(drop=True)
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
    for q in bundle["quantiles"]:
        values = []
        for h in steps:
            ratio = (float(bundle["models"][(h, q)].predict(x)[0]) if use_model
                     else float(bundle["baselineRatio"][(h, q)]))
            margin = 0.0
            for low_q, high_q, _ in BAND_PAIRS:
                if q == low_q:
                    margin = -bundle["margins"].get(f"{h}:{low_q}", 0.0)
                elif q == high_q:
                    margin = bundle["margins"].get(f"{h}:{low_q}", 0.0)
            # 비율을 현재 변동성으로 되돌린다.
            values.append((ratio + margin) * scale)
        raw[q] = values

    horizon = bundle["horizon"]
    grid = np.arange(0, horizon + 1)
    bands: dict[str, list[dict]] = {}
    for q, values in raw.items():
        # 0봉에서는 변화가 없다. 그 점을 넣어야 곡선이 현재 가격에서 출발한다.
        curve = np.interp(grid, [0] + steps, [0.0] + values)
        bands[f"p{int(q * 100)}"] = [
            {"time": int(last_ts // 1000 + step * int(h)),
             "value": last_close * float(np.exp(curve[int(h)]))}
            for h in grid
        ]

    median_final = raw[0.5][-1]
    saved = report(name) or {}
    result = {
        "available": True,
        "model": name,
        "source": "model" if use_model else "volatility-baseline",
        "sourceLabel": "학습 모델" if use_model else "변동성 기준선",
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
        # 방향 분류는 기준선을 못 넘을 때가 많다. 숫자와 함께 그 사실도 같이 낸다.
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
