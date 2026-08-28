"""방향성 분류 모델.

학습과 추론을 나눈다. 화면 요청 때마다 학습하면 느린 것보다, 매번 조금씩 다른 답이
나오는 게 더 나쁘다.

시계열은 무작위 분할을 하면 안 된다. 앞을 학습하고 뒤를 검증하되, 라벨이 미래
`horizon` 봉을 들여다보므로 경계에서 그만큼을 **버린다**(purge). 안 버리면 검증 성적이
실제보다 좋게 나온다 — 학습 데이터가 검증 구간의 결과를 이미 알고 있기 때문이다.
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .features import FEATURE_COLUMNS, build
from .labels import triple_barrier

MODEL_DIR = Path("store_data/models")


class MissingDependency(RuntimeError):
    pass


def _estimator():
    """sklearn 이 없으면 그렇다고 말한다. 없다고 앱이 죽지는 않는다."""
    try:
        from sklearn.ensemble import GradientBoostingClassifier
    except ImportError as exc:
        raise MissingDependency(
            "ML 층은 scikit-learn 이 필요하다: pip install -e '.[ml]'"
        ) from exc
    return GradientBoostingClassifier(
        n_estimators=200, max_depth=3, learning_rate=0.05, subsample=0.8,
        random_state=20260828,
    )


@dataclass
class TrainReport:
    samples: int
    folds: list[dict]
    accuracy: float
    baseline: float
    class_balance: dict
    horizon: int

    def to_dict(self) -> dict:
        return {
            "samples": self.samples,
            "folds": self.folds,
            "accuracy": round(self.accuracy, 4),
            # 항상 다수 클래스만 찍는 모델의 성적. 이걸 못 넘으면 학습된 게 없다.
            "baseline": round(self.baseline, 4),
            "classBalance": self.class_balance,
            "horizon": self.horizon,
        }


def dataset(df: pd.DataFrame, horizon: int = 10, **barrier) -> tuple[pd.DataFrame, pd.Series]:
    features = build(df)
    if features.empty:
        return pd.DataFrame(), pd.Series(dtype="float64")
    labels = triple_barrier(df.tail(len(features)).reset_index(drop=True),
                            horizon=horizon, **barrier)
    frame = features[FEATURE_COLUMNS].join(labels["label"])
    frame = frame.dropna()
    return frame[FEATURE_COLUMNS], frame["label"].astype(int)


def walk_forward(X: pd.DataFrame, y: pd.Series, horizon: int, folds: int = 4) -> TrainReport:
    """앞 → 뒤 순서로 접어 가며 검증한다."""
    estimator_factory = _estimator
    size = len(X)
    if size < 200:
        raise ValueError(f"학습 표본이 {size}개뿐이다 — 최소 200개는 필요하다")

    step = size // (folds + 1)
    reports: list[dict] = []
    correct = total = 0
    for fold in range(folds):
        train_end = step * (fold + 1)
        test_start = train_end + horizon      # purge: 라벨이 겹치는 구간을 버린다
        test_end = min(size, test_start + step)
        if test_end - test_start < 20:
            continue
        model = estimator_factory()
        model.fit(X.iloc[:train_end], y.iloc[:train_end])
        predicted = model.predict(X.iloc[test_start:test_end])
        actual = y.iloc[test_start:test_end]
        hits = int((predicted == actual.to_numpy()).sum())
        correct += hits
        total += len(actual)
        reports.append({
            "fold": fold + 1,
            "trainSize": train_end,
            "testSize": len(actual),
            "accuracy": round(hits / len(actual), 4),
        })

    if total == 0:
        raise ValueError("검증할 구간이 남지 않았다 — 데이터가 더 필요하다")
    counts = y.value_counts()
    return TrainReport(
        samples=size,
        folds=reports,
        accuracy=correct / total,
        baseline=float(counts.max() / counts.sum()),
        class_balance={str(k): int(v) for k, v in counts.items()},
        horizon=horizon,
    )


def train(df: pd.DataFrame, name: str, horizon: int = 10, **barrier) -> dict:
    X, y = dataset(df, horizon=horizon, **barrier)
    if X.empty:
        raise ValueError("피처를 만들 만큼 봉이 없다")
    report = walk_forward(X, y, horizon)
    model = _estimator()
    model.fit(X, y)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with (MODEL_DIR / f"{name}.pkl").open("wb") as handle:
        pickle.dump({"model": model, "columns": FEATURE_COLUMNS, "horizon": horizon}, handle)
    (MODEL_DIR / f"{name}.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report.to_dict()


def predict(df: pd.DataFrame, name: str) -> dict:
    path = MODEL_DIR / f"{name}.pkl"
    if not path.is_file():
        return {"available": False, "reason": f"학습된 모델이 없다: {name}"}
    with path.open("rb") as handle:
        bundle = pickle.load(handle)

    features = build(df)
    if features.empty:
        return {"available": False, "reason": "피처를 만들 만큼 봉이 없다"}
    row = features[bundle["columns"]].dropna().tail(1)
    if row.empty:
        return {"available": False, "reason": "마지막 봉의 지표가 아직 덜 데워졌다"}

    model = bundle["model"]
    proba = model.predict_proba(row)[0]
    classes = [int(c) for c in model.classes_]
    ranked = dict(zip(classes, (float(p) for p in proba)))
    direction = max(ranked, key=ranked.get)
    return {
        "available": True,
        "direction": direction,
        "confidence": ranked[direction],
        "probabilities": {str(k): round(v, 4) for k, v in ranked.items()},
        "horizon": bundle["horizon"],
        "model": name,
    }


def report(name: str) -> dict | None:
    path = MODEL_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
